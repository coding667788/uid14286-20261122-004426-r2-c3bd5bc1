"""compare：旧证据视图与补录修订后新视图的历史覆盖差异。

* cover 口径：逐段比较两视图的最优指派，报告首个受影响段与全部差异；
* schedule 口径：重放旧证据视图下已发布的最优计划（不重新优化、不回填历史），
  按新视图逐段核对席位指派与交接见证，给出最早实质失效段与失效原因。
"""

from __future__ import annotations

from .errors import NO_PUBLISHED_PLAN, RuntimeToolError
from .solver_cover import solve_cover
from .solver_schedule import solve_schedule
from .timeline import EvidenceView


# ---- cover 口径 ----------------------------------------------------------


def _cover_fingerprint(seg: dict) -> tuple:
    return tuple(
        (
            a["position_id"],
            a["seat_id"],
            a["department"],
            a["person"],
            tuple(a["qual_ids"]),
        )
        for a in seg["assignments"]
    )


def compare_cover(case, old_cutoff: int, new_cutoff: int) -> dict:
    old_view = EvidenceView(case, old_cutoff)
    new_view = EvidenceView(case, new_cutoff)
    old_result = solve_cover(old_view)
    new_result = solve_cover(new_view)

    # 新旧视图切段可能不同（补录事件带来新边界），按并集边界统一对齐
    boundaries = sorted(
        {b for s in old_result["segments"] for b in (s["start"], s["end"])}
        | {b for s in new_result["segments"] for b in (s["start"], s["end"])}
    )
    windows = list(zip(boundaries, boundaries[1:]))
    old_by_window = {
        (s["start"], s["end"]): s for s in old_result["segments"]
    }
    new_by_window = {
        (s["start"], s["end"]): s for s in new_result["segments"]
    }

    def lookup(by_window, start, end):
        for (lo, hi), seg in by_window.items():
            if lo <= start and end <= hi:
                return seg
        return None

    diffs = []
    first_affected = None
    for start, end in windows:
        old_seg = lookup(old_by_window, start, end)
        new_seg = lookup(new_by_window, start, end)
        old_fp = _cover_fingerprint(old_seg) if old_seg else None
        new_fp = _cover_fingerprint(new_seg) if new_seg else None
        if old_fp != new_fp:
            entry = {
                "start": start,
                "end": end,
                "old_complete": old_seg["complete"] if old_seg else False,
                "new_complete": new_seg["complete"] if new_seg else False,
                "old_filled": old_seg["filled_seats"] if old_seg else 0,
                "new_filled": new_seg["filled_seats"] if new_seg else 0,
            }
            if first_affected is None:
                first_affected = dict(entry)
            diffs.append(entry)

    return {
        "mode": "cover",
        "old_cutoff": old_cutoff,
        "new_cutoff": new_cutoff,
        "first_affected_segment": first_affected,
        "affected_segment_count": len(diffs),
        "segments_changed": diffs,
        "new_cover": new_result,
    }


# ---- schedule 口径 -------------------------------------------------------


def compare_schedule(case, old_cutoff: int, new_cutoff: int, published: dict) -> dict:
    new_view = EvidenceView(case, new_cutoff)
    old_plan = published["result"]
    replay = _replay_schedule(new_view, old_plan)
    return {
        "mode": "schedule",
        "old_cutoff": old_cutoff,
        "new_cutoff": new_cutoff,
        "published_run_id": published["run_id"],
        "first_failed_segment": replay["first_failed_segment"],
        "replay": replay["checks"],
        "old_plan_kept": True,  # 旧计划即使退化也不回填、不重新优化
        "new_optimal_schedule": solve_schedule(new_view),
    }


def _replay_schedule(view: EvidenceView, old_plan: dict) -> dict:
    pos_by_id = {p.position_id: p for p in view.case.positions}
    checks = []
    first_failed = None
    old_segments = old_plan["segments"]

    def containing_old_segment(seg):
        """新视图切段可能落在旧计划某个更长的段内部。"""
        for old in old_segments:
            if old["start"] <= seg.start and seg.end <= old["end"]:
                return old
        return None

    for seg in view.segments:
        old_seg = containing_old_segment(seg)
        split = old_seg is not None and (
            old_seg["start"] != seg.start or old_seg["end"] != seg.end
        )
        if old_seg is None:
            failure = "not_covered_by_old_plan"
            if first_failed is None:
                first_failed = _fail_entry(seg, failure)
            checks.append(_check_row(seg, False, failure, [failure], split))
            continue

        snap = view.segment_snapshot(seg)
        reasons = _verify_assignments(view, seg, snap, old_seg, pos_by_id)
        if reasons:
            if first_failed is None:
                first_failed = _fail_entry(seg, reasons[0])
            checks.append(_check_row(seg, False, reasons[0], reasons, split))
        else:
            checks.append(_check_row(seg, True, None, [], split))

    # 交接见证核对
    handover_failures = _verify_handovers(view, old_plan)
    for hf in handover_failures:
        if first_failed is None or hf["start"] < first_failed["start"] or (
            hf["start"] == first_failed["start"]
            and first_failed.get("reason", "").startswith("witness") is False
        ):
            first_failed = {
                "start": hf["start"],
                "end": hf["end"],
                "reason": hf["reason"],
            }
        checks.append(
            {
                "start": hf["start"],
                "end": hf["end"],
                "valid": False,
                "reason": hf["reason"],
                "all_reasons": [hf["reason"]],
                "split_by_new_evidence": False,
            }
        )

    checks.sort(key=lambda c: (c["start"], c["end"], c["reason"] or ""))
    return {"first_failed_segment": first_failed, "checks": checks}


def _verify_assignments(view, seg, snap, old_seg, pos_by_id):
    reasons = []
    expected_keys = {
        (p.position_id, s.seat_id)
        for pid in seg.active_positions
        for p in [pos_by_id[pid]]
        for s in p.seats
    }
    seen_persons = set()
    old_by_key = {
        (a["position_id"], a["seat_id"]): a for a in old_seg["assignments"]
    }
    for key in expected_keys:
        a = old_by_key.get(key)
        if a is None:
            reasons.append("seat_missing_in_old_plan")
            continue
        person, qual_ids = a["person"], a["qual_ids"]
        if person in seen_persons:
            reasons.append("duplicate_person")
        seen_persons.add(person)
        if view.is_excluded(person, key[0], seg.start):
            reasons.append("cooling_exclusion")
            continue
        covered = set()
        dept_ok = False
        for qid in qual_ids:
            aq = snap.get(qid)
            if aq is None:
                reasons.append(f"qualification_inactive:{qid}")
                continue
            if aq.person != person:
                reasons.append(f"qualification_holder_changed:{qid}")
                continue
            dept_ok = aq.department == next(
                s.department
                for s in pos_by_id[key[0]].seats
                if s.seat_id == key[1]
            )
            covered.update(aq.categories)
        if qual_ids and not dept_ok:
            reasons.append("department_mismatch")
        if not view.covers_all(
            frozenset(covered), pos_by_id[key[0]].required_categories
        ):
            reasons.append("required_category_uncovered")
    return reasons


def _verify_handovers(view: EvidenceView, old_plan: dict):
    failures = []
    seg_by_window = {(s.start, s.end): s for s in view.segments}
    old_seg_by_window = {
        (s["start"], s["end"]): s for s in old_plan["segments"]
    }
    for h in old_plan.get("handovers", []):
        left_window = next(
            (
                (s.start, s.end)
                for s in view.segments
                if s.end == h["boundary_time"]
            ),
            None,
        )
        right_window = next(
            (
                (s.start, s.end)
                for s in view.segments
                if s.start == h["boundary_time"]
            ),
            None,
        )
        left = seg_by_window.get(left_window) if left_window else None
        right = seg_by_window.get(right_window) if right_window else None
        if left is None or right is None:
            failures.append(
                {
                    "start": h["boundary_time"],
                    "end": h["boundary_time"],
                    "reason": "handover_boundary_split",
                }
            )
            continue
        reason = _check_one_handover(
            view, h, left, right,
            old_seg_by_window.get(left_window),
            old_seg_by_window.get(right_window),
        )
        if reason:
            failures.append(
                {"start": left.start, "end": right.end, "reason": reason}
            )
    return failures


def _check_one_handover(view, h, left, right, old_left, old_right):
    pos = next(p for p in view.case.positions if p.position_id == h["position_id"])
    if pos.handover_department is None:
        return "handover_rule_removed"
    witness = h["witness"]
    # 不能兼任旧计划在该交界两侧的任何在岗席位
    busy = set()
    for old_seg in (old_left, old_right):
        if old_seg is not None:
            busy.update(a["person"] for a in old_seg["assignments"])
    if witness in busy:
        return "witness_occupies_seat"
    left_snap = view.segment_snapshot(left)
    right_snap = view.segment_snapshot(right)
    cats = set()
    for qid in h["witness_qual_ids"]:
        la, ra = left_snap.get(qid), right_snap.get(qid)
        if la is None or ra is None:
            return f"witness_qualification_inactive:{qid}"
        if la.person != witness or ra.person != witness:
            return f"witness_qualification_holder_changed:{qid}"
        if la.department != pos.handover_department:
            return "witness_department_mismatch"
        cats.update(la.categories)
        cats.update(ra.categories)
    if not view.covers_all(frozenset(cats), pos.handover_categories):
        return "witness_category_uncovered"
    return None


def _fail_entry(seg, reason: str) -> dict:
    return {"start": seg.start, "end": seg.end, "reason": reason}


def _check_row(seg, valid: bool, reason, all_reasons=None, split: bool = False):
    return {
        "start": seg.start,
        "end": seg.end,
        "valid": valid,
        "reason": reason,
        "all_reasons": all_reasons or ([] if valid else [reason]),
        "split_by_new_evidence": split,
    }


def run_compare(case, old_cutoff: int | None, new_cutoff: int | None,
                store=None, mode: str | None = None) -> dict:
    q = case.query
    old_cutoff = q.get("old_cutoff") if old_cutoff is None else old_cutoff
    new_cutoff = q.get("new_cutoff") if new_cutoff is None else new_cutoff
    if old_cutoff is None or new_cutoff is None:
        raise RuntimeToolError(
            "QUERY_MISMATCH",
            "compare 需要 old_cutoff 与 new_cutoff（query 或命令参数）",
            "$.query",
        )

    if mode is None:
        # 自动模式：库中存在该 case 的已发布排班则走 schedule 重放
        mode = "cover"
        if store is not None and store.latest(case.case_id, "schedule") is not None:
            mode = "schedule"

    if mode == "schedule":
        if store is None:
            raise RuntimeToolError(
                NO_PUBLISHED_PLAN,
                "schedule 口径 compare 需要 --db 指定持久化库",
                "$.db",
            )
        published = store.latest(case.case_id, "schedule")
        if published is None:
            raise RuntimeToolError(
                NO_PUBLISHED_PLAN,
                f"库中没有 case {case.case_id} 的已发布排班计划",
                "$.db",
            )
        return compare_schedule(case, old_cutoff, new_cutoff, published)
    return compare_cover(case, old_cutoff, new_cutoff)
