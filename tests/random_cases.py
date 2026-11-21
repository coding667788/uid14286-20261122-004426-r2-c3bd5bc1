"""确定性随机小夹具生成（固定种子），供独立朴素对照测试使用。

不依赖第三方库，random.Random 固定种子保证可复现。
"""

from __future__ import annotations

import random

from fixtures.generate_cover import CATEGORIES, base_event


CATEGORY_IDS = ["PUB", "CLS", "SEC", "TOP", "COMMS", "CRYPTO"]
DEPTS = ["OPS", "AUDIT", "WIT"]


def build_random_case(seed: int, with_handover: bool = True) -> dict:
    rnd = random.Random(seed)
    n_persons = rnd.randint(4, 7)
    persons = [f"p{i}" for i in range(n_persons)]
    n_positions = rnd.randint(1, 2)

    events = []
    qseq = 0
    # 每人 1~3 份资质，时刻边界从 {0, 20, 40, 60} 中取，制造 2~3 段
    for person in persons:
        for _ in range(rnd.randint(1, 3)):
            qseq += 1
            dept = rnd.choice(DEPTS)
            cats = rnd.sample(CATEGORY_IDS, rnd.randint(1, 2))
            t0 = rnd.choice([0, 20])
            t1 = rnd.choice([40, 60])
            if t0 >= t1:
                t0, t1 = 0, 60
            events.append(
                base_event(
                    f"Q{qseq}", 1, person, dept, cats, t0, t1,
                    entered_at=10,
                )
            )

    positions = []
    for i in range(n_positions):
        pid = f"P{i}"
        req = rnd.sample(["PUB", "CLS", "SEC"], rnd.randint(1, 2))
        positions.append(
            {
                "position_id": pid,
                "required_categories": req,
                "seats": [
                    {"seat_id": "S1", "department": "OPS"},
                    {"seat_id": "S2", "department": "AUDIT"},
                ],
                "min_span": 1,
                "handover": (
                    {"department": "WIT", "categories": ["PUB"]}
                    if with_handover
                    else None
                ),
            }
        )

    return {
        "case_id": f"random-{seed}",
        "categories": CATEGORIES,
        "qual_events": events,
        "appointments": [],
        "cooling_rules": [],
        "positions": positions,
        "service_combos": [
            {
                "combo_id": "C1",
                "positions": [p["position_id"] for p in positions],
                "from_time": 0,
                "to_time": 60,
            }
        ],
        "query": {"cutoff": 100, "from_time": 0, "to_time": 60},
    }
