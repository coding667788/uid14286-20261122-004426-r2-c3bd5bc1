"""逐段覆盖求解器（约束搜索为主体）。

目标（每段独立）：
1. 优先完整指派；否则最大化已填席位数；
2. 最小化该段使用的资质数；
3. 按 (岗位, 部门, 人, 资质 ID) 的字典序决胜。

搜索以席位为变量回溯，人员唯一性、同部门多资质并集覆盖、双签同人排除、
冷却排除都在变量域内表达；Kuhn 最大匹配做前向可行性剪枝。
"""

from __future__ import annotations

from .errors import RuntimeToolError, SEARCH_LIMIT
from .matching import max_matching
from .model import Position, SeatSpec, SegmentAssignment
from .timeline import ActiveQual, EvidenceView

SEARCH_NODE_LIMIT = 1_000_000


def min_qual_sets_for_person(
    view: EvidenceView,
    quals: list[ActiveQual],
    required_categories: tuple[str, ...],
) -> tuple[str, ...] | None:
    """同一人同一部门的多份资质并集覆盖全部要求时，返回资质数最少的集合。

    同数时取资质 ID 字典序最小的组合，保证决胜确定性。
    """
    best: tuple[str, ...] | None = None

    def dfs(start: int, chosen: list[str], covered: frozenset[str]) -> None:
        nonlocal best
        if best is not None and len(chosen) >= len(best):
            return
        if view.covers_all(covered, required_categories):
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


def build_seat_options(
    view: EvidenceView,
    seg_active: dict[str, ActiveQual],
    pos: Position,
    seat: SeatSpec,
    seg_time: int,
):
    """席位在某段的全部用人方案 [(qual_ids, person), ...]。"""
    by_person: dict[str, list[ActiveQual]] = {}
    for aq in seg_active.values():
        if aq.department != seat.department:
            continue
        if view.is_excluded(aq.person, pos.position_id, seg_time):
            continue
        by_person.setdefault(aq.person, []).append(aq)

    options = []
    for person in sorted(by_person):
        quals = sorted(by_person[person], key=lambda q: q.qual_id)
        qual_ids = min_qual_sets_for_person(view, quals, pos.required_categories)
        if qual_ids is not None:
            options.append((qual_ids, person))
    # 先按资质数、再按资质 ID 元组、再按人排序
    options.sort(key=lambda x: (len(x[0]), x[0], x[1]))
    return options


class CoverSolver:
    def __init__(self, view: EvidenceView):
        self.view = view
        self.nodes = 0

    def solve_segment(self, seg):
        view = self.view
        snap = view.segment_snapshot(seg)
        demand = view.demand_seats(seg)
        seat_keys = [(pos.position_id, seat.seat_id) for pos, seat in demand]

        # 每席位：选项列表与可行人员集合
        options_map: dict[tuple[str, str], list] = {}
        eligible: dict[tuple[str, str], set[str]] = {}
        for pos, seat in demand:
            opts = build_seat_options(view, snap, pos, seat, seg.start)
            key = (pos.position_id, seat.seat_id)
            options_map[key] = opts
            eligible[key] = {person for _, person in opts}

        total = len(seat_keys)
        maximum = len(max_matching({k: eligible[k] for k in seat_keys}))

        if maximum == total:
            assignment, filled = self._search(seat_keys, options_map, eligible, total, total)
            complete = True
        else:
            assignment, filled = self._search(seat_keys, options_map, eligible, maximum, total)
            complete = False

        rows = self._to_rows(demand, assignment)
        return {
            "complete": complete,
            "filled_seats": filled,
            "total_seats": total,
            "empty_seats": [
                {"position_id": pid, "seat_id": sid}
                for (pid, sid) in seat_keys
                if (pid, sid) not in assignment
            ],
            "assignments": rows,
        }

    # ---- 回溯 ------------------------------------------------------------

    def _search(self, seat_keys, options_map, eligible, target_fill, total):
        remaining_cache: dict[tuple, bool] = {}
        best: dict | None = None
        best_qual = None
        best_key = None
        assignment: dict[tuple, tuple] = {}
        # 每席位保留每人的最优（资质数最少、字典序最小）方案，去除同人冗余分支
        unique_options = {
            seat: self._dedup_by_person(options_map[seat]) for seat in seat_keys
        }
        # 每席位至少消耗一份资质，作为 qual_count 的固定下界
        min_quals_per_seat = {
            seat: (len(unique_options[seat][0][0]) if unique_options[seat] else 0)
            for seat in seat_keys
        }

        def forward_feasible(remaining, used, need) -> bool:
            if need == 0:
                return True
            cache_key = (tuple(remaining), tuple(sorted(used)), need)
            cached = remaining_cache.get(cache_key)
            if cached is not None:
                return cached
            sub = {
                seat: {p for p in eligible[seat] if p not in used}
                for seat in remaining
            }
            ok = len(max_matching(sub)) >= need
            remaining_cache[cache_key] = ok
            return ok

        def dfs(remaining, used_persons, filled, qual_count):
            nonlocal best, best_key, best_qual
            self.nodes += 1
            if self.nodes > SEARCH_NODE_LIMIT:
                raise RuntimeToolError(
                    SEARCH_LIMIT,
                    f"覆盖搜索节点超过上限 {SEARCH_NODE_LIMIT}，请缩小夹具规模",
                )
            need = target_fill - filled
            if need < 0 or not forward_feasible(tuple(remaining), used_persons, max(need, 0)):
                return

            # 下界剪枝：当前资质数 + 剩余待填席的最小资质消耗
            lower_bound = qual_count
            if need > 0:
                lower_bound += sum(
                    sorted(min_quals_per_seat[s] for s in remaining)[:need]
                )
            if best_qual is not None:
                if lower_bound > best_qual:
                    return
                # 字典序前缀剪枝仅在完整模式可靠（部分模式含空席占位）
                if (
                    target_fill == total
                    and lower_bound == best_qual
                    and self._prefix_worse(seat_keys, assignment, best_key)
                ):
                    return

            if not remaining:
                if filled != target_fill:
                    return
                key = self._solution_key(seat_keys, assignment, target_fill, total)
                if best is None or (qual_count, key) < (best_qual, best_key):
                    best = dict(assignment)
                    best_qual = qual_count
                    best_key = key
                return

            seat = self._pick_mrv(remaining, eligible, used_persons)
            rest = [s for s in remaining if s != seat]
            opts = unique_options[seat]

            if target_fill == total:
                # 完整模式：每席必须填
                for qual_ids, person in opts:
                    if person in used_persons:
                        continue
                    assignment[seat] = (person, qual_ids)
                    dfs(rest, used_persons | {person},
                        filled + 1, qual_count + len(qual_ids))
                    assignment.pop(seat)
            else:
                # 部分模式：先尝试填，再尝试留空（留空须仍能达到 target_fill）
                for qual_ids, person in opts:
                    if person in used_persons:
                        continue
                    assignment[seat] = (person, qual_ids)
                    dfs(rest, used_persons | {person},
                        filled + 1, qual_count + len(qual_ids))
                    assignment.pop(seat)
                if target_fill - filled <= len(rest) and forward_feasible(
                    tuple(rest), used_persons, target_fill - filled
                ):
                    dfs(rest, used_persons, filled, qual_count)

        dfs(list(seat_keys), frozenset(), 0, 0)
        if best is None:
            return {}, 0
        return best, target_fill

    @staticmethod
    def _prefix_worse(seat_keys, assignment, best_key) -> bool:
        """已固定席位的前缀字典序是否严格劣于现任最优（未固定席位不参与）。"""
        for i, seat in enumerate(seat_keys):
            if seat not in assignment:
                continue
            person, qual_ids = assignment[seat]
            current = (seat[0], seat[1], person, tuple(qual_ids))
            if current > best_key[i]:
                return True
            if current < best_key[i]:
                return False
        return False

    @staticmethod
    def _dedup_by_person(options):
        seen: dict[str, tuple] = {}
        for qual_ids, person in options:
            if person not in seen or (
                len(qual_ids), qual_ids
            ) < (len(seen[person]), seen[person]):
                seen[person] = qual_ids
        out = [(q, p) for p, q in seen.items()]
        out.sort(key=lambda x: (len(x[0]), x[0], x[1]))
        return out

    @staticmethod
    def _pick_mrv(remaining, eligible, used_persons):
        return min(
            remaining,
            key=lambda s: (
                len({p for p in eligible[s] if p not in used_persons}),
                s,
            ),
        )

    @staticmethod
    def _solution_key(seat_keys, assignment, target_fill, total):
        rows = []
        for seat in seat_keys:
            if seat in assignment:
                person, qual_ids = assignment[seat]
                rows.append((seat[0], seat[1], person, tuple(qual_ids)))
            else:
                rows.append((seat[0], seat[1], "", ()))
        return tuple(rows)

    @staticmethod
    def _to_rows(demand, assignment):
        rows = []
        for pos, seat in demand:
            key = (pos.position_id, seat.seat_id)
            if key in assignment:
                person, qual_ids = assignment[key]
                rows.append(
                    SegmentAssignment(
                        position_id=pos.position_id,
                        seat_id=seat.seat_id,
                        department=seat.department,
                        person=person,
                        qual_ids=tuple(qual_ids),
                    )
                )
            else:
                rows.append(
                    SegmentAssignment(
                        position_id=pos.position_id,
                        seat_id=seat.seat_id,
                        department=seat.department,
                        person=None,
                        qual_ids=(),
                    )
                )
        return rows


def solve_cover(view: EvidenceView) -> dict:
    solver = CoverSolver(view)
    segments_out = []
    for seg in view.segments:
        result = solver.solve_segment(seg)
        segments_out.append(
            {
                "segment_index": seg.index,
                "start": seg.start,
                "end": seg.end,
                "active_positions": list(seg.active_positions),
                "complete": result["complete"],
                "filled_seats": result["filled_seats"],
                "total_seats": result["total_seats"],
                "empty_seats": result["empty_seats"],
                "assignments": [
                    {
                        "position_id": a.position_id,
                        "seat_id": a.seat_id,
                        "department": a.department,
                        "person": a.person,
                        "qual_ids": list(a.qual_ids),
                    }
                    for a in result["assignments"]
                ],
            }
        )
    return {
        "cutoff": view.cutoff,
        "segments": segments_out,
        "search_nodes": solver.nodes,
    }
