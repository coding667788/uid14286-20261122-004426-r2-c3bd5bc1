"""证据视图、更正解析与事件边界切段。

核心概念：
* 证据录入切点 ``cutoff``：只保留 ``entered_at <= cutoff`` 的事件；
* 更正事件在自己声明的 ``[effective_from, effective_to)`` 内替换被替换版本，
  该区间外回落到基础版本；
* 全部有效事件边界（授予/终止/更正区间/服务组合/冷却窗口）切段后，
  每段内资质状态、岗位需求、排除关系全部恒定。
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Case, QualEvent, Segment


@dataclass(frozen=True)
class ActiveQual:
    """某段内处于有效状态的一份资质（解析更正后的具体版本）。"""

    qual_id: str
    person: str
    department: str
    categories: tuple[str, ...]
    version: int


class EvidenceView:
    def __init__(self, case: Case, cutoff: int):
        self.case = case
        self.cutoff = cutoff
        self.closure = build_closure(case.categories)
        self.visible = tuple(
            e for e in case.events if e.entered_at <= cutoff
        )
        self._bases: dict[str, QualEvent] = {}
        self._corrections: dict[str, list[QualEvent]] = {}
        for ev in self.visible:
            if ev.replaces is None:
                self._bases[ev.qual_id] = ev
            else:
                self._corrections.setdefault(ev.qual_id, []).append(ev)
        self.exclusions = self._build_exclusions()
        self.segments = self._build_segments()

    # ---- 类别 ------------------------------------------------------------

    def covers(self, held_categories, required: str) -> bool:
        """持有类别（含自身闭包）是否覆盖要求类别。"""
        for c in held_categories:
            if required in self.closure.get(c, ()):
                return True
        return False

    def covers_all(self, held_categories, required_categories) -> bool:
        return all(self.covers(held_categories, r) for r in required_categories)

    # ---- 更正解析 --------------------------------------------------------

    def effective_event(self, qual_id: str, t: int) -> QualEvent | None:
        base = self._bases.get(qual_id)
        if base is None:
            return None
        chosen = base
        for corr in self._corrections.get(qual_id, ()):
            # 同一被替换版本的更正区间互叠已在校验阶段拒绝
            if corr.replaces == base.version and corr.effective_from <= t < corr.effective_to:
                chosen = corr
                break
        return chosen

    def active_quals_at(self, t: int) -> list[ActiveQual]:
        out: list[ActiveQual] = []
        for qual_id in self._bases:
            ev = self.effective_event(qual_id, t)
            if ev is not None and ev.grant <= t < ev.terminate:
                out.append(
                    ActiveQual(
                        qual_id=ev.qual_id,
                        person=ev.person,
                        department=ev.department,
                        categories=ev.categories,
                        version=ev.version,
                    )
                )
        return out

    # ---- 冷却排除 --------------------------------------------------------

    def _build_exclusions(self) -> dict[tuple[str, str], list[tuple[int, int]]]:
        """(人, 受限岗位) -> 禁用半开区间列表。"""
        rules_by_controlled: dict[str, list] = {}
        for rule in self.case.cooling_rules:
            rules_by_controlled.setdefault(rule.controlled_position, []).append(rule)
        excl: dict[tuple[str, str], list[tuple[int, int]]] = {}
        for ap in self.case.appointments:
            for rule in rules_by_controlled.get(ap.position, ()):
                # 签任结束时刻起算冷却；半开区间使结束点恰可上岗
                excl.setdefault((ap.person, rule.barred_position), []).append(
                    (ap.to_time, ap.to_time + rule.cooldown)
                )
        for intervals in excl.values():
            intervals.sort()
        return excl

    def is_excluded(self, person: str, position_id: str, t: int) -> bool:
        for lo, hi in self.exclusions.get((person, position_id), ()):
            if lo <= t < hi:
                return True
            if lo > t:
                break
        return False

    # ---- 切段 ------------------------------------------------------------

    def query_window(self) -> tuple[int, int]:
        q = self.case.query
        if "from_time" in q and "to_time" in q:
            return q["from_time"], q["to_time"]
        bounds: set[int] = set()
        for ev in self.visible:
            bounds.add(ev.grant)
            bounds.add(ev.terminate)
            if ev.replaces is not None:
                bounds.add(ev.effective_from)
                bounds.add(ev.effective_to)
        for combo in self.case.combos:
            bounds.add(combo.from_time)
            bounds.add(combo.to_time)
        if not bounds:
            return 0, 0
        return min(bounds), max(bounds)

    def _all_boundaries(self) -> set[int]:
        bounds: set[int] = set()
        for ev in self.visible:
            bounds.add(ev.grant)
            bounds.add(ev.terminate)
            if ev.replaces is not None:
                bounds.add(ev.effective_from)
                bounds.add(ev.effective_to)
        for combo in self.case.combos:
            bounds.add(combo.from_time)
            bounds.add(combo.to_time)
        for intervals in self.exclusions.values():
            for lo, hi in intervals:
                bounds.add(lo)
                bounds.add(hi)
        return bounds

    def _build_segments(self) -> list[Segment]:
        win_lo, win_hi = self.query_window()
        if win_lo >= win_hi:
            return []
        raw = sorted(b for b in self._all_boundaries() if win_lo < b < win_hi)
        cuts = [win_lo, *raw, win_hi]
        segments: list[Segment] = []
        for i, (lo, hi) in enumerate(zip(cuts, cuts[1:])):
            if lo >= hi:
                continue
            t = lo  # 半开区间内状态恒定，取左端点代表整段
            active = sorted(
                {
                    pid
                    for combo in self.case.combos
                    if combo.from_time <= t < combo.to_time
                    for pid in combo.positions
                }
            )
            segments.append(Segment(index=len(segments), start=lo, end=hi,
                                    active_positions=tuple(active)))
        # 重排索引（裁剪后仍连续）
        for i, seg in enumerate(segments):
            seg.index = i
        return segments

    # ---- 段内快查 --------------------------------------------------------

    def segment_snapshot(self, seg: Segment) -> dict[str, ActiveQual]:
        """段内每资质 ID 的有效版本（无效则不出现）。"""
        t = seg.start
        snap: dict[str, ActiveQual] = {}
        for aq in self.active_quals_at(t):
            snap[aq.qual_id] = aq
        return snap

    def demand_seats(self, seg: Segment):
        """段内需要开席的 (岗位, 席位) 列表，按岗位/席位排序。"""
        out = []
        pos_by_id = {p.position_id: p for p in self.case.positions}
        for pid in seg.active_positions:
            pos = pos_by_id[pid]
            for seat in pos.seats:
                out.append((pos, seat))
        return out


def build_closure(categories: dict[str, str | None]) -> dict[str, frozenset[str]]:
    """每个类别 -> 含自身与全部上位类别的闭包。

    持有上位类别覆盖下位：判断 ``required in closure(held)``。
    """
    cache: dict[str, frozenset[str]] = {}

    def of(cid: str) -> frozenset[str]:
        if cid in cache:
            return cache[cid]
        chain = {cid}
        cur = categories.get(cid)
        guard = 0
        while cur is not None and guard < len(categories) + 1:
            chain.add(cur)
            cur = categories.get(cur)
            guard += 1
        cache[cid] = frozenset(chain)
        return cache[cid]

    return {cid: of(cid) for cid in categories}
