"""compare 与 CLI / SQLite 持久化集成测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from solution.compare import run_compare
from solution.db import PlanStore
from solution.errors import RuntimeToolError
from solution.timeline import EvidenceView
from solution.validate import load_case

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


def load_fixture(name: str):
    return load_case(
        json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    )


class CoverCompareTests(unittest.TestCase):
    def test_first_affected_segment_is_middle(self):
        case = load_fixture("cover_correction_middle.json")
        diff = run_compare(case, 50, 100, store=None)
        self.assertEqual(diff["mode"], "cover")
        first = diff["first_affected_segment"]
        self.assertEqual((first["start"], first["end"]), (30, 60))
        self.assertTrue(first["old_complete"])
        self.assertFalse(first["new_complete"])
        self.assertEqual(diff["affected_segment_count"], 1)

    def test_no_change_when_cutoffs_identical(self):
        case = load_fixture("cover_correction_middle.json")
        diff = run_compare(case, 50, 50, store=None)
        self.assertIsNone(diff["first_affected_segment"])
        self.assertEqual(diff["affected_segment_count"], 0)


class ScheduleCompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "plans.sqlite")
        self.store = PlanStore(self.db_path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_published_plan_first_substantive_failure(self):
        case = load_fixture("schedule_replay_compare.json")
        # 旧证据视图发布计划
        from solution.solver_schedule import solve_schedule

        old = solve_schedule(EvidenceView(case, 50))
        self.assertTrue(old["feasible"])
        self.store.publish(case.case_id, "schedule", 50, old)

        diff = run_compare(case, 50, 80, store=self.store)
        self.assertEqual(diff["mode"], "schedule")
        first = diff["first_failed_segment"]
        self.assertEqual((first["start"], first["end"]), (40, 60))
        # 旧计划保留，不回填
        self.assertTrue(diff["old_plan_kept"])
        # 新视图自身最优也无解
        self.assertFalse(diff["new_optimal_schedule"]["feasible"])
        valid_rows = [c for c in diff["replay"] if c["valid"]]
        self.assertEqual(len(valid_rows), 2)  # 两侧段仍有效

    def test_old_plan_not_reoptimized(self):
        case = load_fixture("schedule_replay_compare.json")
        from solution.solver_schedule import solve_schedule

        old = solve_schedule(EvidenceView(case, 50))
        self.store.publish(case.case_id, "schedule", 50, old)
        run_compare(case, 50, 80, store=self.store)
        # 已发布计划原样保留在库中，不被 compare 回填或改写
        stored = self.store.latest(case.case_id, "schedule")
        self.assertEqual(
            stored["result"]["segments"], old["segments"]
        )
        persons = {
            a["person"]
            for seg in stored["result"]["segments"]
            for a in seg["assignments"]
        }
        self.assertEqual(persons, {"alice", "dan"})


class CliIntegrationTests(unittest.TestCase):
    def test_cli_cover_outputs_utf8_json_and_exit_zero(self):
        proc = subprocess.run(
            [sys.executable, "-m", "solution", "cover",
             str(FIXTURES / "cover_demo.json")],
            capture_output=True, cwd=ROOT,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8"))
        payload = json.loads(proc.stdout.decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertIn("segments", payload["result"])

    def test_cli_validation_error_exit_two_with_field_and_code(self):
        bad = {"case_id": "x"}  # 缺所有必填字段
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps(bad), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "solution", "cover", str(path)],
                capture_output=True, cwd=ROOT,
            )
            self.assertEqual(proc.returncode, 2)
            payload = json.loads(proc.stdout.decode("utf-8"))
            self.assertFalse(payload["ok"])
            for err in payload["errors"]:
                self.assertTrue(err["field"].startswith("$"))
                self.assertTrue(err["code"].isupper() or "_" in err["code"])

    def test_cli_schedule_publish_and_compare_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "db.sqlite")
            case_path = FIXTURES / "schedule_replay_compare.json"
            pub = subprocess.run(
                [sys.executable, "-m", "solution", "schedule",
                 str(case_path), "--cutoff", "50", "--db", db],
                capture_output=True, cwd=ROOT,
            )
            self.assertEqual(pub.returncode, 0, pub.stderr.decode("utf-8"))
            published = json.loads(pub.stdout.decode("utf-8"))
            self.assertIn("published_run_id", published["result"])

            cmp_proc = subprocess.run(
                [sys.executable, "-m", "solution", "compare",
                 str(case_path), "--db", db],
                capture_output=True, cwd=ROOT,
            )
            self.assertEqual(cmp_proc.returncode, 0, cmp_proc.stderr.decode("utf-8"))
            diff = json.loads(cmp_proc.stdout.decode("utf-8"))["result"]
            self.assertEqual(diff["mode"], "schedule")
            self.assertEqual(
                (diff["first_failed_segment"]["start"],
                 diff["first_failed_segment"]["end"]),
                (40, 60),
            )

    def test_cli_compare_without_db_runs_cover_mode(self):
        proc = subprocess.run(
            [sys.executable, "-m", "solution", "compare",
             str(FIXTURES / "cover_correction_middle.json")],
            capture_output=True, cwd=ROOT,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8"))
        diff = json.loads(proc.stdout.decode("utf-8"))["result"]
        self.assertEqual(diff["mode"], "cover")


class QueryValidationTests(unittest.TestCase):
    def test_missing_cutoff_raises_query_mismatch(self):
        case = load_fixture("cover_demo.json")
        broken = type(case)(
            case_id=case.case_id, categories=case.categories, events=case.events,
            appointments=case.appointments, cooling_rules=case.cooling_rules,
            positions=case.positions, combos=case.combos, query={},
        )
        with self.assertRaises(RuntimeToolError) as ctx:
            run_compare(broken, None, None, store=None)
        self.assertEqual(ctx.exception.code, "QUERY_MISMATCH")

    def test_schedule_mode_without_db_raises_no_published_plan(self):
        case = load_fixture("schedule_replay_compare.json")
        with self.assertRaises(RuntimeToolError) as ctx:
            run_compare(case, 50, 80, store=None, mode="schedule")
        self.assertEqual(ctx.exception.code, "NO_PUBLISHED_PLAN")

    def test_explicit_cover_mode_ignores_published_schedule(self):
        case = load_fixture("schedule_replay_compare.json")
        from solution.solver_schedule import solve_schedule

        with tempfile.TemporaryDirectory() as tmp:
            store = PlanStore(str(Path(tmp) / "db.sqlite"))
            store.publish(
                case.case_id, "schedule", 50,
                solve_schedule(EvidenceView(case, 50)),
            )
            diff = run_compare(case, 50, 80, store=store, mode="cover")
            self.assertEqual(diff["mode"], "cover")
            store.close()


if __name__ == "__main__":
    unittest.main()
