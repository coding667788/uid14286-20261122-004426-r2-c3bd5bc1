"""cover 求解器测试：题目点名场景 + 独立朴素对照 + 校验错误定位。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from solution.errors import ValidationFailure
from solution.naive import naive_cover
from solution.solver_cover import solve_cover
from solution.timeline import EvidenceView, build_closure
from solution.validate import load_case
from tests.random_cases import build_random_case

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load_fixture(name: str):
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return load_case(raw)


def run_cover(name: str, cutoff=None):
    case = load_fixture(name)
    cutoff = case.query["cutoff"] if cutoff is None else cutoff
    return solve_cover(EvidenceView(case, cutoff))


def segment_summary(result):
    return [
        (s["start"], s["end"], s["complete"], s["filled_seats"], s["total_seats"])
        for s in result["segments"]
    ]


class HierarchyTests(unittest.TestCase):
    def test_upper_category_chain_covers_lower(self):
        case = load_fixture("cover_hierarchy.json")
        closure = build_closure(case.categories)
        # 上位覆盖下位：TOP 闭包包含 SEC/CLS/PUB
        self.assertTrue({"SEC", "CLS", "PUB"} <= closure["TOP"])
        # 反向不成立：CLS 不覆盖 SEC
        self.assertNotIn("SEC", closure["CLS"])
        result = run_cover("cover_hierarchy.json")
        seg = result["segments"][0]
        self.assertTrue(seg["complete"])
        ops = next(
            a for a in seg["assignments"]
            if a["department"] == "OPS" and a["person"]
        )
        # OPS 席位两人都可行，字典序 sec_holder 在前；单独验证 TOP 闭包可覆盖
        self.assertIn(ops["person"], {"sec_holder", "top_holder"})
        view = EvidenceView(case, case.query["cutoff"])
        self.assertTrue(view.covers(["TOP"], "SEC"))
        self.assertFalse(view.covers(["CLS"], "SEC"))

    def test_hierarchy_on_random_small_cases(self):
        for seed in range(12):
            case = load_case(build_random_case(seed))
            view = EvidenceView(case, 100)
            closure = build_closure(case.categories)
            for cid, chain in closure.items():
                self.assertIn(cid, chain)


class ContentionTests(unittest.TestCase):
    def test_cross_position_contention(self):
        result = run_cover("cover_contention.json")
        first, second = result["segments"]
        # 第一段 OPS 两人 -> 四席填满
        self.assertTrue(first["complete"])
        self.assertEqual(first["filled_seats"], 4)
        # 第二段 carol 失效，OPS 仅 alice，两 OPS 席争用 -> 最多 3 席
        self.assertFalse(second["complete"])
        self.assertEqual(second["filled_seats"], 3)
        self.assertEqual(len(second["empty_seats"]), 1)
        # 同一人不会在同段占两席
        for seg in result["segments"]:
            used = [a["person"] for a in seg["assignments"] if a["person"]]
            self.assertEqual(len(used), len(set(used)))


class DualSignFailureTests(unittest.TestCase):
    def test_same_department_dual_sign_cannot_fill(self):
        result = run_cover("cover_dual_sign_fail.json")
        seg = result["segments"][0]
        self.assertFalse(seg["complete"])
        self.assertEqual(seg["filled_seats"], 1)
        empty = seg["empty_seats"]
        self.assertEqual(empty, [{"position_id": "VAULT", "seat_id": "V2"}])
        # 两个 OPS 持证人不能被同时塞进同岗双签
        persons = [a["person"] for a in seg["assignments"] if a["person"]]
        self.assertEqual(persons, ["alice"])


class CooldownTests(unittest.TestCase):
    def test_eligible_exactly_when_cooldown_ends(self):
        result = run_cover("cover_cooldown.json")
        summaries = segment_summary(result)
        # [20,40) 完整；[40,70) heidi 冷却 -> 空 OPS 席；70 恰可上岗 -> 完整
        self.assertEqual(
            summaries,
            [(20, 40, True, 2, 2), (40, 70, False, 1, 2), (70, 90, True, 2, 2)],
        )
        last = result["segments"][2]
        guard_ops = next(
            a for a in last["assignments"]
            if a["position_id"] == "GUARD" and a["department"] == "OPS"
        )
        self.assertEqual(guard_ops["person"], "heidi")  # 70 时刻冷却刚结束


class CorrectionMiddleTests(unittest.TestCase):
    def test_correction_changes_only_middle_segment(self):
        result = run_cover("cover_correction_middle.json", cutoff=100)
        summaries = segment_summary(result)
        self.assertEqual(
            summaries,
            [(0, 30, True, 2, 2), (30, 60, False, 1, 2), (60, 90, True, 2, 2)],
        )
        # 旧证据切点看不到补录更正 -> 全程完整
        old = run_cover("cover_correction_middle.json", cutoff=50)
        self.assertEqual(len(old["segments"]), 1)
        self.assertTrue(old["segments"][0]["complete"])


class PartialAssignmentTests(unittest.TestCase):
    def test_maximal_partial_with_empty_seat_marks(self):
        result = run_cover("cover_partial.json")
        seg = result["segments"][0]
        self.assertFalse(seg["complete"])
        self.assertEqual(seg["filled_seats"], 1)
        self.assertEqual(seg["empty_seats"],
                         [{"position_id": "GUARD", "seat_id": "G2"}])


class MultiQualUnionTests(unittest.TestCase):
    def test_same_department_multi_qual_union_and_minimal_set(self):
        result = run_cover("cover_demo.json")
        seg0 = result["segments"][0]
        radio = next(
            a for a in seg0["assignments"]
            if a["position_id"] == "RADIO" and a["department"] == "OPS"
        )
        # frank 两份拼接 vs erin 单证同时覆盖 CLS+COMMS -> 最小资质数选 erin
        self.assertEqual(radio["person"], "erin")
        self.assertEqual(radio["qual_ids"], ["Q-ERIN"])


class NaiveCrossCheckCoverTests(unittest.TestCase):
    """独立朴素穷举与约束搜索在随机小夹具上逐段等价。"""

    def test_equivalent_to_naive_enumeration(self):
        for seed in range(20):
            with self.subTest(seed=seed):
                case = load_case(build_random_case(seed))
                view = EvidenceView(case, 100)
                fast = solve_cover(view)
                naive = naive_cover(EvidenceView(case, 100))
                self.assertEqual(len(fast["segments"]), len(naive))
                for seg, ref in zip(fast["segments"], naive):
                    self.assertEqual(
                        seg["filled_seats"], ref["filled"],
                        f"seed {seed} seg {seg['segment_index']} 填充数不一致",
                    )
                    fast_persons = sorted(
                        (a["position_id"], a["seat_id"], a["person"],
                         tuple(a["qual_ids"]))
                        for a in seg["assignments"] if a["person"]
                    )
                    naive_persons = sorted(
                        (ref["demand"][i][0].position_id,
                         ref["demand"][i][1].seat_id, c[0], tuple(c[1]))
                        for i, c in enumerate(ref["combo"]) if c is not None
                    )
                    # 同目标序下两实现应给出同一字典序最优解
                    self.assertEqual(fast_persons, naive_persons)


class ValidationErrorTests(unittest.TestCase):
    def test_errors_carry_field_path_and_code(self):
        raw = json.loads((FIXTURES / "cover_demo.json").read_text(encoding="utf-8"))
        raw["qual_events"][0]["grant"] = 999  # grant >= terminate
        raw["positions"][0]["seats"] = raw["positions"][0]["seats"][:1]  # 只留一席
        with self.assertRaises(ValidationFailure) as ctx:
            load_case(raw)
        codes = {e.code for e in ctx.exception.errors}
        fields = {e.field for e in ctx.exception.errors}
        self.assertIn("INVALID_VALUE", codes)
        self.assertTrue(any(f.startswith("$.qual_events[0]") for f in fields))
        self.assertTrue(any("seats" in f for f in fields))

    def test_unknown_reference_and_cycle(self):
        raw = json.loads((FIXTURES / "cover_demo.json").read_text(encoding="utf-8"))
        raw["qual_events"][0]["categories"] = ["GHOST"]
        raw["categories"].append({"id": "X", "parent": "Y"})
        raw["categories"].append({"id": "Y", "parent": "X"})
        with self.assertRaises(ValidationFailure) as ctx:
            load_case(raw)
        codes = {e.code for e in ctx.exception.errors}
        self.assertIn("UNKNOWN_REFERENCE", codes)
        self.assertIn("CYCLE_DETECTED", codes)


if __name__ == "__main__":
    unittest.main()
