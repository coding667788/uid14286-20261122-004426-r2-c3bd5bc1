"""连续排班测试：题目点名四个场景、最短跨度、见证争用、朴素对照。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from solution.naive import naive_schedule
from solution.solver_schedule import solve_schedule
from solution.timeline import EvidenceView
from solution.validate import load_case
from tests.random_cases import build_random_case

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load_fixture(name: str):
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return load_case(raw)


def run_schedule(name: str, cutoff=None):
    case = load_fixture(name)
    cutoff = case.query["cutoff"] if cutoff is None else cutoff
    return solve_schedule(EvidenceView(case, cutoff))


class SegmentwiseFeasibleButNoHandoverTests(unittest.TestCase):
    def test_each_segment_feasible_but_continuous_fails(self):
        from solution.solver_cover import solve_cover

        case = load_fixture("schedule_no_handover.json")
        view = EvidenceView(case, 80)
        cover = solve_cover(view)
        # 每段独立都有解
        self.assertTrue(all(s["complete"] for s in cover["segments"]))
        # 但没有 WIT 部门见证人 -> 连续排班失败
        result = solve_schedule(view)
        self.assertFalse(result["feasible"])
        self.assertEqual(
            result["failure"]["reason"], "no_legal_continuous_schedule"
        )


class WitnessContentionTests(unittest.TestCase):
    def test_single_witness_contested_by_two_positions(self):
        result = run_schedule("schedule_witness_contention.json")
        self.assertFalse(result["feasible"])
        # 对照：若再补一名见证人，则两岗可同时完成交接
        case = load_fixture("schedule_witness_contention.json")
        extra = (
            type(case.events[0])(
                qual_id="W2", version=1, person="wally2", department="WIT",
                categories=("CLS",), grant=0, terminate=80, replaces=None,
                effective_from=0, effective_to=0, entered_at=1,
            ),
        )
        relaxed = type(case)(
            case_id=case.case_id, categories=case.categories,
            events=case.events + extra, appointments=case.appointments,
            cooling_rules=case.cooling_rules, positions=case.positions,
            combos=case.combos, query=case.query,
        )
        result = solve_schedule(EvidenceView(relaxed, 80))
        self.assertTrue(result["feasible"])
        self.assertEqual(result["changes"], 2)  # 两岗各换一次
        witnesses = {h["witness"] for h in result["handovers"]}
        self.assertEqual(len(witnesses), 2)  # 两名见证人分担


class PartialRecallTests(unittest.TestCase):
    def test_multi_qual_stitch_partially_revoked_fails(self):
        result = run_schedule("schedule_partial_recall.json")
        self.assertFalse(result["feasible"])
        # 第一段（t<30）frank 两证拼接仍可独立覆盖
        from solution.solver_cover import solve_cover

        case = load_fixture("schedule_partial_recall.json")
        cover = solve_cover(EvidenceView(case, 80))
        self.assertTrue(cover["segments"][0]["complete"])
        self.assertFalse(cover["segments"][1]["complete"])


class SharedEmployeeTests(unittest.TestCase):
    def test_global_optimum_prioritizes_zero_changes(self):
        # 共享员工场景：alice/bob 都能填两个 OPS 席，逐段字典序最优会把
        # alice 优先放到每个岗位；连续排班的全局目标先压换人次数，
        # 因此每岗跨段锁定同一人，changes 必须为 0。
        result = run_schedule("schedule_shared_employee.json")
        self.assertTrue(result["feasible"])
        self.assertEqual(result["changes"], 0)
        seg0, seg1 = result["segments"]

        def seat(seg, pid, sid):
            return next(
                a for a in seg["assignments"]
                if a["position_id"] == pid and a["seat_id"] == sid
            )

        # 同一人两段同岗（续期换资质但不换人）
        for pid in ("PA", "PB"):
            self.assertEqual(
                seat(seg0, pid, "S1")["person"],
                seat(seg1, pid, "S1")["person"],
            )
        # 同段一人最多一席：两个 OPS 席必须分给 alice/bob 两人
        ops_seg0 = {
            seat(seg0, pid, "S1")["person"] for pid in ("PA", "PB")
        }
        self.assertEqual(ops_seg0, {"alice", "bob"})
        for seg in result["segments"]:
            persons = [a["person"] for a in seg["assignments"]]
            self.assertEqual(len(persons), len(set(persons)))
        # 续期：同一人第二段使用新资质 ID（在岗者以同部门另一份有效资质继续）
        self.assertNotEqual(
            seat(seg0, "PA", "S1")["qual_ids"],
            seat(seg1, "PA", "S1")["qual_ids"],
        )

    def test_forcing_a_change_is_strictly_worse_so_solver_rejects_it(self):
        # 构造一个“换人但资质更少”的诱惑：换人能省资质时，仍应优先 0 换人
        result = run_schedule("schedule_shared_employee.json")
        self.assertEqual(result["changes"], 0)
        # 对照朴素穷举，两者目标键首位（换人次数）一致
        case = load_fixture("schedule_shared_employee.json")
        ref = naive_schedule(EvidenceView(case, 90))
        self.assertTrue(ref["feasible"])
        self.assertEqual(ref["key"][0], result["changes"])


class HandoverOkTests(unittest.TestCase):
    def test_legal_handover_with_third_party_witness(self):
        result = run_schedule("schedule_handover_ok.json")
        self.assertTrue(result["feasible"])
        self.assertEqual(result["changes"], 1)
        self.assertEqual(len(result["handovers"]), 1)
        h = result["handovers"][0]
        self.assertEqual(h["boundary_time"], 30)
        self.assertEqual(h["old_person"], "alice")
        self.assertEqual(h["new_person"], "bob")
        self.assertEqual(h["witness"], "wally")
        # 见证人不占任何在岗席位
        busy = {
            a["person"]
            for seg in result["segments"]
            for a in seg["assignments"]
        }
        self.assertNotIn("wally", busy)


class MinSpanTests(unittest.TestCase):
    def test_min_span_violation_makes_schedule_fail(self):
        result = run_schedule("schedule_min_span.json")
        self.assertFalse(result["feasible"])

    def test_min_span_relaxed_then_handover_succeeds(self):
        case = load_fixture("schedule_min_span.json")
        positions = []
        for pos in case.positions:
            positions.append(
                type(pos)(
                    position_id=pos.position_id,
                    required_categories=pos.required_categories,
                    seats=pos.seats,
                    min_span=1,  # 放宽
                    handover_department=pos.handover_department,
                    handover_categories=pos.handover_categories,
                )
            )
        relaxed = type(case)(
            case_id=case.case_id, categories=case.categories, events=case.events,
            appointments=case.appointments, cooling_rules=case.cooling_rules,
            positions=tuple(positions), combos=case.combos, query=case.query,
        )
        result = solve_schedule(EvidenceView(relaxed, 90))
        self.assertTrue(result["feasible"])
        self.assertEqual(result["changes"], 1)


class WitnessCannotHoldSeatTests(unittest.TestCase):
    def test_witness_occupied_as_seat_is_rejected(self):
        # 让唯一见证人同时是唯一 AUDIT 持证人 -> 交接时必占席位，无解
        case = load_fixture("schedule_handover_ok.json")
        events = [
            e for e in case.events
            if not (e.person == "dan" and e.department == "AUDIT")
        ]
        from solution.model import QualEvent

        events.append(
            QualEvent(
                qual_id="W2", version=1, person="wally", department="AUDIT",
                categories=("SEC",), grant=0, terminate=90, replaces=None,
                effective_from=0, effective_to=0, entered_at=1,
            )
        )
        mutated = type(case)(
            case_id=case.case_id, categories=case.categories,
            events=tuple(events), appointments=case.appointments,
            cooling_rules=case.cooling_rules, positions=case.positions,
            combos=case.combos, query=case.query,
        )
        result = solve_schedule(EvidenceView(mutated, 90))
        self.assertFalse(result["feasible"])


class ObjectiveOrderTests(unittest.TestCase):
    def test_min_changes_then_min_quals_then_lexicographic(self):
        result = run_schedule("schedule_handover_ok.json")
        self.assertTrue(result["feasible"])
        # 资质总数：三段边界共两段，每段两席各一证 -> 4
        self.assertEqual(result["qual_total"], 4)
        # 输出按段/岗/席位排序
        for seg in result["segments"]:
            keys = [(a["position_id"], a["seat_id"]) for a in seg["assignments"]]
            self.assertEqual(keys, sorted(keys))


class MinSpanExemptionTests(unittest.TestCase):
    def _case_with(self, min_span, combos):
        from solution.model import ServiceCombo

        case = load_fixture("schedule_handover_ok.json")
        pos = type(case.positions[0])(
            position_id=case.positions[0].position_id,
            required_categories=case.positions[0].required_categories,
            seats=case.positions[0].seats, min_span=min_span,
            handover_department=case.positions[0].handover_department,
            handover_categories=case.positions[0].handover_categories,
        )
        return type(case)(
            case_id=case.case_id, categories=case.categories, events=case.events,
            appointments=case.appointments, cooling_rules=case.cooling_rules,
            positions=(pos,),
            combos=tuple(
                ServiceCombo(c[0], ("GUARD",), c[1], c[2]) for c in combos
            ),
            query=case.query,
        )

    def test_natural_gap_restarts_run_without_handover(self):
        # 两段需求中间隔着无需求段：运行天然断开，min_span 不跨段累计、无需见证
        case = self._case_with(2, [("C1", 0, 30), ("C2", 60, 90)])
        result = solve_schedule(EvidenceView(case, 90))
        self.assertTrue(result["feasible"])
        self.assertEqual(result["changes"], 0)
        self.assertEqual(result["handovers"], [])
        # 无需求段仍出现在输出中，带空指派
        empty = next(s for s in result["segments"] if s["start"] == 30)
        self.assertEqual(empty["assignments"], [])

    def test_tail_closing_segment_exempt_from_min_span(self):
        # 时间线末尾收尾段不足 min_span 时豁免
        case = self._case_with(2, [("C1", 0, 30)])
        result = solve_schedule(EvidenceView(case, 90))
        self.assertTrue(result["feasible"])


class NaiveCrossCheckScheduleTests(unittest.TestCase):
    def test_equivalent_to_naive_enumeration(self):
        checked = 0
        for seed in range(30):
            case = load_case(build_random_case(seed))
            view = EvidenceView(case, 100)
            if len(view.segments) > 5:
                continue
            ref = naive_schedule(EvidenceView(case, 100))
            if ref is None:
                continue
            checked += 1
            fast = solve_schedule(view)
            self.assertEqual(fast["feasible"], ref["feasible"], f"seed {seed}")
            if ref["feasible"]:
                self.assertEqual(fast["changes"], ref["key"][0], f"seed {seed}")
                self.assertEqual(fast["qual_total"], ref["key"][1], f"seed {seed}")
        self.assertGreater(checked, 10)  # 确保对照确实执行了足够多样例


class ScaleLimitTests(unittest.TestCase):
    def test_schedule_rejects_oversized_case(self):
        from solution.errors import RuntimeToolError

        case = load_fixture("schedule_handover_ok.json")
        # 构造 21 名持证人
        from solution.model import QualEvent

        extra = tuple(
            QualEvent(
                qual_id=f"BIG{i}", version=1, person=f"big{i}",
                department="OPS", categories=("SEC",), grant=0,
                terminate=90, replaces=None, effective_from=0,
                effective_to=0, entered_at=1,
            )
            for i in range(21)
        )
        mutated = type(case)(
            case_id=case.case_id, categories=case.categories,
            events=case.events + extra, appointments=case.appointments,
            cooling_rules=case.cooling_rules, positions=case.positions,
            combos=case.combos, query=case.query,
        )
        with self.assertRaises(RuntimeToolError) as ctx:
            solve_schedule(EvidenceView(mutated, 90))
        self.assertEqual(ctx.exception.code, "SCALE_LIMIT")


if __name__ == "__main__":
    unittest.main()
