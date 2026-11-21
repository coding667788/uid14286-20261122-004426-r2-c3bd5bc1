"""规模压力夹具（50 持证人 / 15 岗位），用于验证 cover 上限内可解。

复现：python fixtures/generate_scale.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fixtures.generate_cover import CATEGORIES, base_event, write


def generate_scale():
    events = []
    qid = 0
    # 50 名持证人：OPS/AUDIT 各 20 人（15 岗位各需两席），WIT 10 人
    # 每 4 人一名 TOP 持证人，其余 SEC/CLS；部分人额外持有 COMMS 以覆盖拼接岗位
    def dept_for(i):
        if i < 20:
            return "OPS"
        if i < 40:
            return "AUDIT"
        return "WIT"

    for i in range(50):
        person = f"person{i:02d}"
        dept = dept_for(i)
        qid += 1
        if i % 4 == 0:
            cats = ["TOP"]
        elif i % 2 == 0:
            cats = ["SEC"]
        else:
            cats = ["CLS", "COMMS"]
        events.append(base_event(f"Q{qid:03d}", 1, person, dept, cats, 0, 200))
        if i % 5 == 0:
            qid += 1
            events.append(
                base_event(f"Q{qid:03d}", 1, person, dept, ["COMMS"], 0, 200)
            )

    positions = []
    for j in range(15):
        req = ["SEC"] if j % 3 else ["CLS", "COMMS"]
        positions.append(
            {
                "position_id": f"POS{j:02d}",
                "required_categories": req,
                "seats": [
                    {"seat_id": "S1", "department": "OPS"},
                    {"seat_id": "S2", "department": "AUDIT"},
                ],
                "min_span": 1,
                "handover": None,
            }
        )

    case = {
        "case_id": "scale_50_15",
        "categories": CATEGORIES,
        "qual_events": events,
        "appointments": [],
        "cooling_rules": [],
        "positions": positions,
        "service_combos": [
            {
                "combo_id": "SC1",
                "positions": [f"POS{j:02d}" for j in range(15)],
                "from_time": 0,
                "to_time": 200,
            }
        ],
        "query": {"cutoff": 200, "from_time": 0, "to_time": 200},
    }
    write("scale_50_15.json", case)
    print("scale fixture generated: 50 persons, 15 positions, 30 seats")


if __name__ == "__main__":
    generate_scale()
