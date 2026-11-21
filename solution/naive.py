"""独立小规模对照求解器（朴素穷举）。

与 :mod:`solution.solver_cover` / :mod:`solution.solver_schedule` 完全独立的
实现：直接对所有席位用人组合做笛卡尔积穷举，再逐一过滤约束、按同一目标序
取最优。仅用于小规模随机夹具上的等价性对照，不参与正式求解路径。
"""

from __future__ import annotations

import itertools

from .timeline import EvidenceView

NAIVE_SEAT_LIMIT = 8
NAIVE_SEGMENT_LIMIT = 5
NAIVE_OPTIONS_LIMIT = 6


def _seat_choices(view: EvidenceView, seg, pos, seat):
    """穷举席位候选：(人, 资质组合)；资质组合按覆盖可行性过滤。"""
    snap = view.segment_snapshot(seg)
    by_person: dict[str, list] = {}
    for aq in snap.values():
        if aq.department != seat.department:
            continue
        if view.is_excluded(aq.person, pos.position_id, seg.start):
            continue
        by_person.setdefault(aq.person, []).append(aq)
    choices = []
    for person, quals in by_person.items():
        ids = sorted(q.qual_id for q in quals)
        for r in range(1, len(ids) + 1):
            for combo in itertools.combinations(ids, r):
                cats = frozenset(
                    c for qid in combo for c in snap[qid].categories
                )
                if view.covers_all(cats, pos.required_categories):
                    choices.append((person, tuple(combo)))
    return choices


def naive_cover(view: EvidenceView) -> list[dict]:
    out = []
    for seg in view.segments:
        demand = view.demand_seats(seg)
        domains = []
        for pos, seat in demand:
            choices = _seat_choices(view, seg, pos, seat)
            assert len(choices) <= 200, "朴素对照仅适用于小规模夹具"
            # 留空选项仅在部分指派时需要
            domains.append([None, *choices])
        best = None
        for combo in itertools.product(*domains):
            persons = [c[0] for c in combo if c is not None]
            if len(set(persons)) != len(persons):
                continue
            filled = len(persons)
            qual_count = sum(len(c[1]) for c in combo if c is not None)
            key = (
                -filled,
                qual_count,
                tuple(
                    (
                        demand[i][0].position_id,
                        demand[i][1].seat_id,
                        c[0] if c else "",
                        c[1] if c else (),
                    )
                    for i, c in enumerate(combo)
                ),
            )
            if best is None or key < best[0]:
                best = (key, combo)
        out.append(
            {
                "segment_index": seg.index,
                "filled": sum(1 for c in best[1] if c is not None),
                "combo": best[1],
                "demand": demand,
            }
        )
    return out


def naive_schedule(view: EvidenceView) -> dict | None:
    """朴素穷举连续排班；规模超限时返回 None 表示跳过对照。"""
    if len(view.segments) > NAIVE_SEGMENT_LIMIT:
        return None
    per_segment = []
    for seg in view.segments:
        demand = view.demand_seats(seg)
        if len(demand) > NAIVE_SEAT_LIMIT:
            return None
        domains = []
        for pos, seat in demand:
            choices = _seat_choices(view, seg, pos, seat)
            if len(choices) > NAIVE_OPTIONS_LIMIT:
                return None
            domains.append(choices)
        candidates = []
        for combo in itertools.product(*domains):
            persons = [c[0] for c in combo]
            if len(set(persons)) != len(persons):
                continue
            candidates.append(
                {
                    (demand[i][0].position_id, demand[i][1].seat_id): combo[i]
                    for i in range(len(combo))
                }
            )
        if not candidates:
            return {"feasible": False}
        per_segment.append((demand, candidates))

    best = None
    for plan in itertools.product(*[c for _, c in per_segment]):
        checked = _check_continuous(view, per_segment, plan)
        if checked is None:
            continue
        changes, qual_sum, assign_key, witness_key = checked
        key = (changes, qual_sum, assign_key, witness_key)
        if best is None or key < best[0]:
            best = (key, plan)
    if best is None:
        return {"feasible": False}
    return {"feasible": True, "key": best[0], "plan": best[1]}


def _check_continuous(view, per_segment, plan):
    pos_by_id = {p.position_id: p for p in view.case.positions}
    changes = 0
    qual_sum = 0
    run_len: dict[tuple[str, str], int] = {}
    witness_out = []
    assign_key = []

    for s, (demand, _) in enumerate(per_segment):
        keys = [(pos.position_id, seat.seat_id) for pos, seat in demand]
        assign_key.append(
            tuple(
                (
                    key[0],
                    key[1],
                    next(
                        seat.department
                        for seat in pos_by_id[key[0]].seats
                        if seat.seat_id == key[1]
                    ),
                    plan[s][key][0],
                    plan[s][key][1],
                )
                for key in keys
            )
        )
        for key in keys:
            person, qual_ids = plan[s][key]
            qual_sum += len(qual_ids)
            if s > 0 and key in plan[s - 1]:
                old_person = plan[s - 1][key][0]
                if old_person == person:
                    run_len[key] = run_len.get(key, 0) + 1
                    continue
                pos = pos_by_id[key[0]]
                if run_len.get(key, 0) < pos.min_span:
                    return None
                changes += 1
                run_len[key] = 1
            else:
                run_len[key] = 1

        if s > 0:
            boundary_handovers = _naive_witnesses(view, per_segment, plan, s)
            if boundary_handovers is None:
                return None
            witness_out.append(boundary_handovers)

    last_keys = set(
        (pos.position_id, seat.seat_id) for pos, seat in per_segment[-1][0]
    )
    for key, length in run_len.items():
        if key in last_keys and length < pos_by_id[key[0]].min_span:
            return None

    witness_key = tuple(
        tuple(sorted(boundary, key=lambda h: (h[0], h[1], h[2])))
        for boundary in witness_out
    )
    return changes, qual_sum, tuple(assign_key), witness_key


def _naive_witnesses(view, per_segment, plan, s):
    seg_left = view.segments[s - 1]
    seg_right = view.segments[s]
    boundary_time = seg_left.end
    pos_by_id = {p.position_id: p for p in view.case.positions}
    busy = {c[0] for c in plan[s - 1].values()} | {c[0] for c in plan[s].values()}
    out = []
    used = set()
    left_snap = view.segment_snapshot(seg_left)
    right_snap = view.segment_snapshot(seg_right)
    for key in sorted(plan[s]):
        old_person = plan[s - 1].get(key, (None,))[0]
        if old_person is None or old_person == plan[s][key][0]:
            continue
        pos = pos_by_id[key[0]]
        if pos.handover_department is None:
            return None  # 声明了换人却没配见证规则，无法满足
        cands = _naive_witness_candidates(view, pos, left_snap, right_snap)
        pick = next((p for p in cands if p not in busy and p not in used), None)
        if pick is None:
            return None
        used.add(pick)
        out.append((boundary_time, key[0], key[1], pick))
    return out


def _naive_witness_candidates(view, pos, left_snap, right_snap):
    dept = pos.handover_department
    persons = set()
    for qid, aq in left_snap.items():
        if aq.department != dept or qid not in right_snap:
            continue
        other = right_snap[qid]
        if other.person == aq.person and other.department == dept:
            cats = frozenset(aq.categories) | frozenset(other.categories)
            if view.covers_all(cats, pos.handover_categories):
                persons.add(aq.person)
    return sorted(persons)
