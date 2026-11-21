"""连续排班虚构夹具生成器（确定性）。

复现：python fixtures/generate_schedule.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fixtures.generate_cover import CATEGORIES, HANDOVER_WIT, base_event, write


def guard_position(pid="GUARD", req=("SEC",), min_span=1):
    return {
        "position_id": pid,
        "required_categories": list(req),
        "seats": [
            {"seat_id": "S1", "department": "OPS"},
            {"seat_id": "S2", "department": "AUDIT"},
        ],
        "min_span": min_span,
        "handover": HANDOVER_WIT,
    }


# =========================================================================
# 1. schedule_no_handover：各段独立有解，但找不到合法连续交接
# =========================================================================

def schedule_no_handover():
    events = [
        # S1 仅 alice 持 SEC；S2 起 bob 持 SEC（alice 在 S2 失效）-> 必须换人
        base_event("A1", 1, "alice", "OPS", ["SEC"], 0, 20),
        base_event("B1", 1, "bob", "OPS", ["SEC"], 20, 80),
        # AUDIT 席全程 dan
        base_event("D1", 1, "dan", "AUDIT", ["SEC"], 0, 80),
        # WIT 部门无人持证 -> 不存在第三人见证
    ]
    case = {
        "case_id": "schedule_no_handover",
        "categories": CATEGORIES,
        "qual_events": events,
        "appointments": [],
        "cooling_rules": [],
        "positions": [guard_position()],
        "service_combos": [
            {"combo_id": "C1", "positions": ["GUARD"],
             "from_time": 0, "to_time": 80},
        ],
        "query": {"cutoff": 80, "from_time": 0, "to_time": 80},
    }
    write("schedule_no_handover.json", case)


# =========================================================================
# 2. schedule_witness_contention：交接见证被两岗争用
# =========================================================================

def schedule_witness_contention():
    events = [
        # 岗位 PA 的 OPS 席：S1 alice -> S2 bob
        base_event("PA1", 1, "alice", "OPS", ["SEC"], 0, 20),
        base_event("PA2", 1, "bob", "OPS", ["SEC"], 20, 80),
        # 岗位 PB 的 OPS 席：S1 carol -> S2 erin
        base_event("PB1", 1, "carol", "OPS", ["SEC"], 0, 20),
        base_event("PB2", 1, "erin", "OPS", ["SEC"], 20, 80),
        # 两岗 AUDIT 席：dan/dee 各锁一岗，避免同段人员争用
        base_event("AU1", 1, "dan", "AUDIT", ["SEC"], 0, 80),
        base_event("AU2", 1, "dee", "AUDIT", ["SEC"], 0, 80),
        # 唯一见证：wally 同时是两岗交接的唯一候选 -> 同刻争用失败
        base_event("W1", 1, "wally", "WIT", ["CLS"], 0, 80),
    ]
    case = {
        "case_id": "schedule_witness_contention",
        "categories": CATEGORIES,
        "qual_events": events,
        "appointments": [],
        "cooling_rules": [],
        "positions": [
            guard_position("PA"),
            guard_position("PB"),
        ],
        "service_combos": [
            {"combo_id": "C1", "positions": ["PA", "PB"],
             "from_time": 0, "to_time": 80},
        ],
        "query": {"cutoff": 80, "from_time": 0, "to_time": 80},
    }
    write("schedule_witness_contention.json", case)


# =========================================================================
# 3. schedule_partial_recall：多类别拼接资质被部分回收
# =========================================================================

def schedule_partial_recall():
    # 席位要求 CLS + COMMS；frank 两份资质拼接，Q-F2 在 t=30 失效且无人可补
    events = [
        base_event("F1", 1, "frank", "OPS", ["CLS"], 0, 80),
        base_event("F2", 1, "frank", "OPS", ["COMMS"], 0, 30),
        # 另一 OPS 持证人只覆盖 CLS，无法补 COMMS
        base_event("G1", 1, "gina", "OPS", ["CLS"], 0, 80),
        base_event("AU1", 1, "dan", "AUDIT", ["CLS", "COMMS"], 0, 80),
        base_event("W1", 1, "wally", "WIT", ["CLS"], 0, 80),
    ]
    pos = guard_position("RADIO", req=("CLS", "COMMS"))
    case = {
        "case_id": "schedule_partial_recall",
        "categories": CATEGORIES,
        "qual_events": events,
        "appointments": [],
        "cooling_rules": [],
        "positions": [pos],
        "service_combos": [
            {"combo_id": "C1", "positions": ["RADIO"],
             "from_time": 0, "to_time": 80},
        ],
        "query": {"cutoff": 80, "from_time": 0, "to_time": 80},
    }
    write("schedule_partial_recall.json", case)


# =========================================================================
# 4. schedule_shared_employee：共享员工使全局最优不同于逐段最优
# =========================================================================

def schedule_shared_employee():
    # 两个岗位同刻开席。alice 全程可用但单段只能占一个席位；
    # bob 全程可用。逐段独立最优会倾向 alice（字典序在前），
    # 连续排班为把换人次数压到 0，每岗固定一人，alice/bob 各锁一岗。
    # alice 的资质在 t=45 有一次“同效续期”（旧证终止、新证授予），
    # 既在时间线上切出两段，又允许在岗者以同部门另一份有效资质继续。
    events = [
        base_event("AL1", 1, "alice", "OPS", ["SEC"], 0, 45),
        base_event("AL2", 1, "alice", "OPS", ["SEC"], 45, 90),
        base_event("BO", 1, "bob", "OPS", ["SEC"], 0, 90),
        base_event("AU1", 1, "dan", "AUDIT", ["SEC"], 0, 90),
        base_event("AU2", 1, "dee", "AUDIT", ["SEC"], 0, 90),
        base_event("W1", 1, "wally", "WIT", ["CLS"], 0, 90),
    ]
    case = {
        "case_id": "schedule_shared_employee",
        "categories": CATEGORIES,
        "qual_events": events,
        "appointments": [],
        "cooling_rules": [],
        "positions": [
            guard_position("PA"),
            guard_position("PB"),
        ],
        "service_combos": [
            {"combo_id": "C1", "positions": ["PA", "PB"],
             "from_time": 0, "to_time": 90},
        ],
        "query": {"cutoff": 90, "from_time": 0, "to_time": 90},
    }
    write("schedule_shared_employee.json", case)


# =========================================================================
# 5. schedule_replay_compare：旧证据视图发布，新视图下旧计划实质失效
# =========================================================================

def schedule_replay_compare():
    events = [
        base_event("A1", 1, "alice", "OPS", ["SEC"], 0, 90),
        base_event("D1", 1, "dan", "AUDIT", ["SEC"], 0, 90),
        base_event("W1", 1, "wally", "WIT", ["CLS"], 0, 90),
        # 补录更正：alice 在 [40,60) 降级为 PUB，旧视图（cutoff=50）不可见
        base_event("A1", 2, "alice", "OPS", ["PUB"], 0, 90,
                   replaces=1, eff_from=40, eff_to=60, entered_at=70),
    ]
    case = {
        "case_id": "schedule_replay_compare",
        "categories": CATEGORIES,
        "qual_events": events,
        "appointments": [],
        "cooling_rules": [],
        "positions": [guard_position()],
        "service_combos": [
            {"combo_id": "C1", "positions": ["GUARD"],
             "from_time": 0, "to_time": 90},
        ],
        "query": {"cutoff": 80, "old_cutoff": 50, "new_cutoff": 80,
                  "from_time": 0, "to_time": 90},
    }
    write("schedule_replay_compare.json", case)


# =========================================================================
# 6. schedule_min_span：最短在岗跨度约束（换人会违反 min_span）
# =========================================================================

def schedule_min_span():
    events = [
        base_event("A1", 1, "alice", "OPS", ["SEC"], 0, 30),
        base_event("B1", 1, "bob", "OPS", ["SEC"], 30, 90),
        base_event("D1", 1, "dan", "AUDIT", ["SEC"], 0, 90),
        base_event("W1", 1, "wally", "WIT", ["CLS"], 0, 90),
    ]
    pos = guard_position()
    pos["min_span"] = 2  # alice 只在岗 1 段就被换掉 -> 违反，整体失败
    case = {
        "case_id": "schedule_min_span",
        "categories": CATEGORIES,
        "qual_events": events,
        "appointments": [],
        "cooling_rules": [],
        "positions": [pos],
        "service_combos": [
            {"combo_id": "C1", "positions": ["GUARD"],
             "from_time": 0, "to_time": 90},
        ],
        "query": {"cutoff": 90, "from_time": 0, "to_time": 90},
    }
    write("schedule_min_span.json", case)


# =========================================================================
# 7. schedule_handover_ok：一次合法换人交接（正向路径）
# =========================================================================

def schedule_handover_ok():
    events = [
        # OPS 席：alice 第一段，t=30 后 bob 接手（两人各满 min_span=1）
        base_event("A1", 1, "alice", "OPS", ["SEC"], 0, 30),
        base_event("B1", 1, "bob", "OPS", ["SEC"], 30, 90),
        base_event("D1", 1, "dan", "AUDIT", ["SEC"], 0, 90),
        # 见证 wally 两段都有效，且不占任何席位
        base_event("W1", 1, "wally", "WIT", ["CLS"], 0, 90),
    ]
    case = {
        "case_id": "schedule_handover_ok",
        "categories": CATEGORIES,
        "qual_events": events,
        "appointments": [],
        "cooling_rules": [],
        "positions": [guard_position()],
        "service_combos": [
            {"combo_id": "C1", "positions": ["GUARD"],
             "from_time": 0, "to_time": 90},
        ],
        "query": {"cutoff": 90, "from_time": 0, "to_time": 90},
    }
    write("schedule_handover_ok.json", case)


def main():
    schedule_no_handover()
    schedule_witness_contention()
    schedule_partial_recall()
    schedule_shared_employee()
    schedule_replay_compare()
    schedule_min_span()
    schedule_handover_ok()
    print("schedule fixtures generated")


if __name__ == "__main__":
    main()
