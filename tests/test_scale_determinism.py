"""规模上限与确定性测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from solution.solver_cover import solve_cover
from solution.timeline import EvidenceView
from solution.validate import load_case

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


class ScaleTests(unittest.TestCase):
    def test_cover_50_persons_15_positions_solves_fast(self):
        raw = json.loads((FIXTURES / "scale_50_15.json").read_text(encoding="utf-8"))
        case = load_case(raw)
        persons = {ev.person for ev in case.events}
        self.assertEqual(len(persons), 50)
        self.assertEqual(len(case.positions), 15)
        result = solve_cover(EvidenceView(case, 200))
        seg = result["segments"][0]
        self.assertTrue(seg["complete"])
        self.assertEqual(seg["total_seats"], 30)
        # 约束搜索剪枝有效，不应退化为百万级穷举
        self.assertLess(result["search_nodes"], 100_000)

    def test_schedule_limits_are_20_8_8(self):
        from solution.solver_schedule import (
            MAX_PERSONS_SCHEDULE,
            MAX_POSITIONS_SCHEDULE,
            MAX_SEGMENTS_SCHEDULE,
        )
        self.assertEqual((MAX_PERSONS_SCHEDULE, MAX_POSITIONS_SCHEDULE,
                          MAX_SEGMENTS_SCHEDULE), (20, 8, 8))


class DeterminismTests(unittest.TestCase):
    def test_all_commands_byte_deterministic(self):
        for command, fixture in [
            ("cover", "cover_demo.json"),
            ("cover", "cover_correction_middle.json"),
            ("schedule", "schedule_shared_employee.json"),
            ("compare", "cover_correction_middle.json"),
        ]:
            with self.subTest(command=command, fixture=fixture):
                outputs = set()
                for _ in range(2):
                    proc = subprocess.run(
                        [sys.executable, "-m", "solution", command,
                         str(FIXTURES / fixture)],
                        capture_output=True, cwd=ROOT, check=True,
                    )
                    outputs.add(proc.stdout)
                self.assertEqual(len(outputs), 1)

    def test_schedule_deterministic_with_db(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "p.sqlite")
            outputs = set()
            for _ in range(2):
                proc = subprocess.run(
                    [sys.executable, "-m", "solution", "schedule",
                     str(FIXTURES / "schedule_handover_ok.json"),
                     "--db", db],
                    capture_output=True, cwd=ROOT, check=True,
                )
                payload = json.loads(proc.stdout.decode("utf-8"))
                # run_id 是自增持久态，剔除后逻辑结果须一致
                payload["result"].pop("published_run_id", None)
                outputs.add(json.dumps(payload, sort_keys=True,
                                       ensure_ascii=False))
            self.assertEqual(len(outputs), 1)


if __name__ == "__main__":
    unittest.main()
