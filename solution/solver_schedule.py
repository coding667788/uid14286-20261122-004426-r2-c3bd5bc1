"""连续排班求解器。

在逐段可行的基础上增加跨段约束：

* ``min_span``：席位上同一人的连续在岗段数下限（岗位无需求造成的天然断开、
  以及时间线末尾的收尾段不受此限）；
* 席位换人时，交界时刻必须有与旧段、新段都有效的第三人作交接见证；
  见证绑定岗位自己的见证部门与类别，同一时刻不能兼任其他交接见证或任何在岗席位；
* 在岗者某份资质失效时，可用本人同部门另一份有效资质继续（段间自动换资质），
  若所需类别无法同刻补齐则该段无合法方案；
* 一个人可跨不重叠的岗位区间工作（不同段不同岗），同段仍最多一个席位。

目标序：完整排班 -> 换人次数最少 -> 各段资质数之和最少 ->
按 (段, 岗, 席位, 部门, 人, 资质, 见证 ID) 字典序决胜。

实现为全局逐席回溯（约束搜索）：段内按 MRV 动态选席位，Kuhn 匹配做
前向可行性剪枝，完整解用目标键做分支限界。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import RuntimeToolError, SCALE_LIMIT
from .matching import max_matching
from .model import Position
from .solver_cover import build_seat_options
from .timeline import ActiveQual, EvidenceView

SCHEDULE_NODE_LIMIT = 2_000_000
MAX_PERSONS_SCHEDULE = 20
MAX_POSITIONS_SCHEDULE = 8
MAX_SEGMENTS_SCHEDULE = 8


@dataclass(frozen=True)
class SeatChoice:
    person: str
    qual_ids: tuple[str, ...]


class ScheduleSolver:
    def __init__(self, view: EvidenceView):
        self.view = view
        self.nodes = 0
        self._validate_scale()

    def _validate_scale(self) -> None:
        persons = {ev.person for ev in self.view.visible}
        active_positions = {pid for s in self.view.segments for pid in s.active_positions}
        if len(persons) > MAX_PERSONS_SCHEDULE:
            raise RuntimeToolError(
                SCALE_LIMIT,
                f"连续排班持证人 {len(persons)} 人，超过上限 {MAX_PERSONS_SCHEDULE}",
                "$.qual_events",
            )
        if len(active_positions) > MAX_POSITIONS_SCHEDULE:
            raise RuntimeToolError(
                SCALE_LIMIT,
                f"连续排班涉及岗位 {len(active_positions)} 个，超过上限 {MAX_POSITIONS_SCHEDULE}",
                "$.positions",
            )
        if len(self.view.segments) > MAX_SEGMENTS_SCHEDULE:
            raise RuntimeToolError(
                SCALE_LIMIT,
                f"连续排班段数 {len(self.view.segments)}，超过上限 {MAX_SEGMENTS_SCHEDULE}",
                "$.query",
            )

    # ---- 预计算 ----------------------------------------------------------

    def prepare(self):
        view = self.view
        self.pos_by_id = {p.position_id: p for p in view.case.positions}
        self.segment_data = []
        for seg in view.segments:
            snap = view.segment_snapshot(seg)
            demand = view.demand_seats(seg)
            options_map: dict[tuple[str, str], list[SeatChoice]] = {}
            eligible: dict[tuple[str, str], set[str]] = {}
            for pos, seat in demand:
                opts = build_seat_options(view, snap, pos, seat, seg.start)
                key = (pos.position_id, seat.seat_id)
                options_map[key] = [
                    SeatChoice(person, tuple(q)) for q, person in opts
                ]
                eligible[key] = {c.person for c in options_map[key]}
            self.segment_data.append(
                {
                    "seg": seg,
                    "demand": demand,
                    "keys": [(p.position_id, s.seat_id) for p, s in demand],
                    "options": options_map,
                    "eligible": eligible,
                    "snap": snap,
                }
            )
        self.witness_candidates: list[dict] = [
            {} for _ in range(max(0, len(view.segments) - 1))
        ]
        for b in range(len(self.witness_candidates)):
            self._prepare_witness_boundary(b)

    def _prepare_witness_boundary(self, b: int) -> None:
        left_idx = self._last_demand_at_or_before(b)
        right_idx = self._first_demand_at_or_after(b + 1)
        if left_idx is None or right_idx is None or left_idx == right_idx:
            return
        left, right = self.segment_data[left_idx], self.segment_data[right_idx]
        for pos, seat in left["demand"]:
            key = (pos.position_id, seat.seat_id)
            if pos.handover_department is None:
                continue
            self.witness_candidates[b][key] = self._witness_candidates_for(
                pos, left, right
            )

    def _last_demand_at_or_before(self, idx):
        for j in range(idx, -1, -1):
            if self.segment_data[j]["keys"]:
                return j
        return None

    def _first_demand_at_or_after(self, idx):
        for j in range(idx, len(self.segment_data)):
            if self.segment_data[j]["keys"]:
                return j
        return None

    def _witness_candidates_for(self, pos: Position, left, right):
        view = self.view
        dept = pos.handover_department
        left_q = {qid: aq for qid, aq in left["snap"].items() if aq.department == dept}
        right_q = {qid: aq for qid, aq in right["snap"].items() if aq.department == dept}
        by_person: dict[str, list[ActiveQual]] = {}
        for qid in sorted(set(left_q) & set(right_q)):
            la, ra = left_q[qid], right_q[qid]
            if la.person != ra.person or la.department != ra.department:
                continue  # 更正导致交界两侧版本归属不一致，不能作跨段见证
            by_person.setdefault(la.person, []).append(la)
        out = []
        for person in sorted(by_person):
            quals = sorted(by_person[person], key=lambda q: q.qual_id)
            qual_ids = self._min_witness_quals(view, quals, pos.handover_categories)
            if qual_ids is not None:
                out.append((person, qual_ids))
        out.sort(key=lambda x: (x[0], x[1]))
        return out

    @staticmethod
    def _min_witness_quals(view, quals, required):
        best = None

        def dfs(start, chosen, covered):
            nonlocal best
            if best is not None and len(chosen) >= len(best):
                return
            if view.covers_all(covered, required):
                cand = tuple(sorted(chosen))
                if best is None or len(cand) < len(best) or (
                    len(cand) == len(best) and cand < best
                ):
                    best = cand
                return
            for i in range(start, len(quals)):
                aq = quals[i]
                new_covered = covered | frozenset(aq.categories)
                if new_covered == covered:
                    continue
                chosen.append(aq.qual_id)
                dfs(i + 1, chosen, new_covered)
                chosen.pop()

        dfs(0, [], frozenset())
        return best

    # ---- 全局回溯 --------------------------------------------------------

    def solve(self) -> dict:
        self.prepare()
        if not self.segment_data:
            return self._empty_result()

        self.best_key = None
        self.best_plan = None
        # 工作状态
        self.plan: list[dict[tuple[str, str], SeatChoice]] = []
        self.handovers: list[list[dict]] = []
        self.run_len: dict[tuple[str, str], int] = {}
        self.continued_keys: set[tuple[str, str]] = set()

        self._dfs_segment(0, 0, 0)

        if self.best_plan is None:
            return self._failure_result()
        choices_plan, handovers_plan = self.best_plan
        return self._success_result(choices_plan, handovers_plan)

    def _dfs_segment(self, seg_idx: int, changes: int, qual_sum: int) -> None:
        if seg_idx == len(self.segment_data):
            if not self._close_runs_ok(seg_idx - 1, self.continued_keys):
                return
            key = self._objective_key(changes, qual_sum)
            if self.best_key is None or key < self.best_key:
                self.best_key = key
                self.best_plan = (
                    [dict(ch) for ch in self.plan],
                    [list(b) for b in self.handovers],
                )
            return

        # 分支限界：前缀目标键已不优于已知完整解则剪枝
        if self.best_key is not None:
            prefix = self._partial_key(changes, qual_sum)
            if _lex_prefix_greater(prefix, self.best_key):
                return

        data = self.segment_data[seg_idx]
        if not data["keys"]:
            # 无需求段：记录空指派，运行状态在下次开席时按天然断开处理
            self.plan.append({})
            self._dfs_segment(seg_idx + 1, changes, qual_sum)
            self.plan.pop()
            return

        partial: dict[tuple[str, str], SeatChoice] = {}
        self._dfs_seat(seg_idx, data, partial, frozenset(), changes, qual_sum)

    def _dfs_seat(self, seg_idx, data, partial, used_persons, changes, qual_sum):
        self.nodes += 1
        if self.nodes > SCHEDULE_NODE_LIMIT:
            raise RuntimeToolError(
                SCALE_LIMIT, f"排班搜索节点超过上限 {SCHEDULE_NODE_LIMIT}"
            )
        keys = data["keys"]
        if len(partial) == len(keys):
            self._finish_segment(seg_idx, partial, changes, qual_sum)
            return

        seat = self._pick_mrv(data, partial, used_persons)
        rest_keys = [k for k in keys if k not in partial and k != seat]
        for cand in data["options"][seat]:
            if cand.person in used_persons:
                continue
            # 前向可行性：剩余席位在人员占用后仍可填满
            sub = {
                k: {p for p in data["eligible"][k] if p not in (used_persons | {cand.person})}
                for k in rest_keys
            }
            if len(max_matching(sub)) < len(rest_keys):
                continue
            partial[seat] = cand
            self._dfs_seat(
                seg_idx,
                data,
                partial,
                used_persons | {cand.person},
                changes,
                qual_sum + len(cand.qual_ids),
            )
            partial.pop(seat)

    def _pick_mrv(self, data, partial, used_persons):
        remaining = [k for k in data["keys"] if k not in partial]
        return min(
            remaining,
            key=lambda k: (
                len({p for p in data["eligible"][k] if p not in used_persons}),
                k,
            ),
        )
    def _finish_segment(self, seg_idx, partial, changes, qual_sum):
        new_runs, broken, added_changes, continued = self._transition_runs(seg_idx, partial)
        if new_runs is None:
            return
        boundary = None
        if self.plan:
            boundary = self._build_handovers(seg_idx, partial, broken)
            if boundary is None:
                return
        old_runs = self.run_len
        old_continued = self.continued_keys
        self.plan.append(partial)
        if boundary is not None:
            self.handovers.append(boundary)
        self.run_len = new_runs
        self.continued_keys = set(continued)
        self._dfs_segment(seg_idx + 1, changes + added_changes, qual_sum)
        self.continued_keys = old_continued
        self.run_len = old_runs
        if boundary is not None:
            self.handovers.pop()
        self.plan.pop()

    # ---- 运行长度与换人 --------------------------------------------------

    def _transition_runs(self, seg_idx, partial):
        data = self.segment_data[seg_idx]
        new_runs = dict(self.run_len)
        broken = []
        changes = 0
        continued: list[tuple[str, str]] = []
        prev_idx = self._last_demand_at_or_before(seg_idx - 1)
        prev = self.plan[prev_idx] if prev_idx is not None else None
        # 仅当上一开席段与本段相邻时，运行才可能连续；
        # 中间隔着无需求段 -> 所有运行天然断开，重新起算
        adjacent = prev_idx is not None and prev_idx == seg_idx - 1

        for key in data["keys"]:
            person = partial[key].person
            if adjacent and key in self.run_len and prev is not None and key in prev:
                old_person = prev[key].person
                if old_person == person:
                    new_runs[key] = self.run_len.get(key, 0) + 1
                    continued.append(key)
                    continue
                pos = self.pos_by_id[key[0]]
                if self.run_len.get(key, 0) < pos.min_span:
                    return None, None, 0, None
                broken.append(key)
                changes += 1
                new_runs[key] = 1
            else:
                # 岗位此前无需求或运行已断开（中间隔着无需求段），重新起算
                new_runs[key] = 1
        return new_runs, broken, changes, continued

    def _close_runs_ok(self, last_seg_idx, continued_keys) -> bool:
        # 时间线末尾收尾：只检查末段仍连续的在岗运行；
        # 天然断开/重新起算的运行豁免 min_span
        for key in continued_keys:
            length = self.run_len.get(key, 0)
            pos = self.pos_by_id[key[0]]
            if length < pos.min_span:
                return False
        return True

    # ---- 交接见证 --------------------------------------------------------

    def _build_handovers(self, seg_idx, new_partial, broken_keys):
        data = self.segment_data[seg_idx]
        boundary_time = data["seg"].start
        prev_idx = self._last_demand_at_or_before(seg_idx - 1)
        prev = self.plan[prev_idx] if prev_idx is not None else None
        if prev is None:
            return None if broken_keys else []
        busy = {c.person for c in prev.values()} | {
            c.person for c in new_partial.values()
        }
        # 见证候选按“进入当前段的边界”预计算；跨无需求段时落在更早的边界上
        boundary = seg_idx - 1
        result = []
        used_witnesses = set()
        for key in sorted(broken_keys):
            cands = self.witness_candidates[boundary].get(key, [])
            if not cands:
                # 回退到上一开席段对应的边界（中间隔着无需求段）
                for b in range(boundary - 1, -1, -1):
                    if key in self.witness_candidates[b]:
                        cands = self.witness_candidates[b]
                        break
            picked = next(
                ((p, q) for p, q in cands if p not in busy and p not in used_witnesses),
                None,
            )
            if picked is None:
                return None
            used_witnesses.add(picked[0])
            result.append(
                {
                    "boundary_time": boundary_time,
                    "position_id": key[0],
                    "seat_id": key[1],
                    "old_person": prev[key].person,
                    "new_person": new_partial[key].person,
                    "witness": picked[0],
                    "witness_qual_ids": list(picked[1]),
                }
            )
        return result

    # ---- 目标键 ----------------------------------------------------------

    def _seat_dept(self, key):
        for seat in self.pos_by_id[key[0]].seats:
            if seat.seat_id == key[1]:
                return seat.department
        return ""

    def _partial_key(self, changes, qual_sum):
        # 已完成段的字典序结构 + 当前累计量，用于与完整解做前缀比较
        assign_key = tuple(
            tuple(
                (
                    key[0],
                    key[1],
                    self._seat_dept(key),
                    ch[key].person,
                    ch[key].qual_ids,
                )
                for key in sorted(ch)
            )
            for ch in self.plan
        )
        return (changes, qual_sum, assign_key)

    def _objective_key(self, changes, qual_sum):
        assign_key = tuple(
            tuple(
                (
                    key[0],
                    key[1],
                    self._seat_dept(key),
                    ch[key].person,
                    ch[key].qual_ids,
                )
                for key in sorted(ch)
            )
            for ch in self.plan
        )
        witness_key = tuple(
            tuple(
                (
                    h["boundary_time"],
                    h["position_id"],
                    h["seat_id"],
                    h["witness"],
                    tuple(h["witness_qual_ids"]),
                )
                for h in sorted(boundary, key=lambda x: (x["position_id"], x["seat_id"]))
            )
            for boundary in self.handovers
        )
        return (changes, qual_sum, assign_key, witness_key)

    # ---- 输出 ------------------------------------------------------------

    def _empty_result(self):
        return {
            "feasible": True,
            "changes": 0,
            "qual_total": 0,
            "segments": [],
            "handovers": [],
            "search_nodes": self.nodes,
        }

    def _failure_result(self):
        return {
            "feasible": False,
            "changes": None,
            "qual_total": None,
            "segments": [],
            "handovers": [],
            "search_nodes": self.nodes,
            "failure": {
                "reason": "no_legal_continuous_schedule",
                "message": "不存在满足最短跨度与交接见证约束的完整连续排班",
            },
        }

    def _success_result(self, plan, handovers_plan):
        changes, qual_total = self.best_key[0], self.best_key[1]
        segments_out = []
        for i, ch in enumerate(plan):
            seg = self.segment_data[i]["seg"]
            rows = [
                {
                    "position_id": key[0],
                    "seat_id": key[1],
                    "department": self._seat_dept(key),
                    "person": ch[key].person,
                    "qual_ids": list(ch[key].qual_ids),
                }
                for key in sorted(ch)
            ]
            segments_out.append(
                {
                    "segment_index": seg.index,
                    "start": seg.start,
                    "end": seg.end,
                    "active_positions": list(seg.active_positions),
                    "assignments": rows,
                }
            )
        flat = [h for boundary in handovers_plan for h in boundary]
        return {
            "feasible": True,
            "changes": changes,
            "qual_total": qual_total,
            "segments": segments_out,
            "handovers": flat,
            "search_nodes": self.nodes,
        }


def solve_schedule(view: EvidenceView) -> dict:
    solver = ScheduleSolver(view)
    result = solver.solve()
    result["cutoff"] = view.cutoff
    return result


def _lex_prefix_greater(prefix: tuple, full: tuple) -> bool:
    """前缀字典序比较：prefix 与 full 等长部分比较，更长的 full 不会使前缀更优。"""
    for i, value in enumerate(prefix):
        if value > full[i]:
            return True
        if value < full[i]:
            return False
    return False
