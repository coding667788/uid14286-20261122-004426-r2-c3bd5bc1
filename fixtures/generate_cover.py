"""生成全部本地虚构夹具（纯标准库，确定性输出）。

复现：python fixtures/generate_fixtures.py
生成结果写入 fixtures/*.json，与仓库内提交的文件逐字节一致。
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent


def write(name: str, payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    (HERE / name).write_text(text + "\n", encoding="utf-8")


# ---- 通用类别层级 --------------------------------------------------------

CATEGORIES = [
    {"id": "PUB", "parent": None},
    {"id": "CLS", "parent": "PUB"},
    {"id": "SEC", "parent": "CLS"},
    {"id": "TOP", "parent": "SEC"},
    {"id": "COMMS", "parent": None},
    {"id": "CRYPTO", "parent": "COMMS"},
]


def base_event(qid, version, person, dept, cats, grant, terminate,
               replaces=None, eff_from=None, eff_to=None, entered_at=None):
    ev = {
        "qual_id": qid,
        "version": version,
        "person": person,
        "department": dept,
        "categories": cats,
        "grant": grant,
        "terminate": terminate,
        "replaces": replaces,
        "entered_at": 1 if entered_at is None else entered_at,
    }
    if replaces is not None:
        ev["effective_from"] = eff_from
        ev["effective_to"] = eff_to
    return ev


TWO_SEAT_GUARD = [
    {"seat_id": "G1", "department": "OPS"},
    {"seat_id": "G2", "department": "AUDIT"},
]
HANDOVER_WIT = {"department": "WIT", "categories": ["CLS"]}


# =========================================================================
# 1. cover_demo：综合演示（上位链 / 双签 / 更正 / 冷却 / 多资质并集）
# =========================================================================

def cover_demo():
    events = [
        base_event("Q-ALPHA", 1, "alice", "OPS", ["TOP"], 0, 100),
        base_event("Q-BRAVO", 1, "bob", "OPS", ["SEC"], 0, 100),
        base_event("Q-CAROL", 1, "carol", "AUDIT", ["SEC"], 0, 100),
        base_event("Q-DAVE", 1, "dave", "AUDIT", ["CLS"], 0, 100),
        base_event("Q-ERIN", 1, "erin", "OPS", ["CLS", "COMMS"], 0, 100),
        # 更正：alice 的 TOP 在 [40,60) 被替换为 CLS，只影响中间一段
        base_event("Q-ALPHA", 2, "alice", "OPS", ["CLS"], 0, 100,
                   replaces=1, eff_from=40, eff_to=60, entered_at=5),
        # 多资质并集：frank 两份拼接覆盖 (CLS + COMMS) 岗位
        base_event("Q-FRANK-1", 1, "frank", "OPS", ["CLS"], 0, 100),
        base_event("Q-FRANK-2", 1, "frank", "OPS", ["COMMS"], 0, 100),
        # 冷却当事人：heidi 签任 VAULT 后冷却期内禁 GUARD
        base_event("Q-HEIDI", 1, "heidi", "OPS", ["SEC"], 0, 100),
        base_event("Q-IVAN", 1, "ivan", "AUDIT", ["TOP"], 0, 100),
        # WIT 部门持证人：覆盖 RADIO 第二席要求的 CLS+COMMS（两证拼接）
        base_event("Q-JUDY-1", 1, "judy", "WIT", ["CLS"], 0, 100),
        base_event("Q-JUDY-2", 1, "judy", "WIT", ["COMMS"], 0, 100),
    ]
    case = {
        "case_id": "cover_demo",
        "categories": CATEGORIES,
        "qual_events": events,
        "appointments": [
            {"appointment_id": "AP-1", "person": "heidi",
             "position": "VAULT", "from_time": 10, "to_time": 40},
        ],
        "cooling_rules": [
            {"rule_id": "CR-1", "controlled_position": "VAULT",
             "barred_position": "GUARD", "cooldown": 30},
        ],
        "positions": [
            {"position_id": "GUARD", "required_categories": ["SEC"],
             "seats": TWO_SEAT_GUARD, "min_span": 1,
             "handover": HANDOVER_WIT},
            {"position_id": "VAULT", "required_categories": ["TOP"],
             "seats": TWO_SEAT_GUARD, "min_span": 1,
             "handover": HANDOVER_WIT},
            {"position_id": "RADIO", "required_categories": ["CLS", "COMMS"],
             "seats": [{"seat_id": "R1", "department": "OPS"},
                       {"seat_id": "R2", "department": "WIT"}],
             "min_span": 1, "handover": None},
        ],
        "service_combos": [
            {"combo_id": "SC-ALL", "positions": ["GUARD", "VAULT", "RADIO"],
             "from_time": 0, "to_time": 100},
        ],
        "query": {"cutoff": 100, "from_time": 0, "to_time": 100},
    }
    write("cover_demo.json", case)


# =========================================================================
# 2. cover_hierarchy：上位类别链覆盖
# =========================================================================

def cover_hierarchy():
    # S2 要求 SEC：OPS 的 top_holder 凭上位链覆盖；S2 的 cls_holder 不覆盖
    # （CLS 是 SEC 的下位），pub_holder 同样不覆盖，故 AUDIT 席无人 -> 部分指派，
    # 另在断言中直接验证上位链的闭包方向。
    events = [
        base_event("H1", 1, "top_holder", "OPS", ["TOP"], 0, 50),
        base_event("H2", 1, "sec_holder", "OPS", ["SEC"], 0, 50),
        base_event("H3", 1, "pub_holder", "AUDIT", ["PUB"], 0, 50),
        base_event("H4", 1, "cls_holder", "AUDIT", ["CLS"], 0, 50),
        base_event("H5", 1, "top_auditor", "AUDIT", ["TOP"], 0, 50),
    ]
    case = {
        "case_id": "cover_hierarchy",
        "categories": CATEGORIES,
        "qual_events": events,
        "appointments": [],
        "cooling_rules": [],
        "positions": [
            {"position_id": "P-SEC", "required_categories": ["SEC"],
             "seats": [{"seat_id": "S1", "department": "OPS"},
                       {"seat_id": "S2", "department": "AUDIT"}],
             "min_span": 1, "handover": None},
        ],
        "service_combos": [
            {"combo_id": "C1", "positions": ["P-SEC"],
             "from_time": 0, "to_time": 50},
        ],
        "query": {"cutoff": 50, "from_time": 0, "to_time": 50},
    }
    write("cover_hierarchy.json", case)


# =========================================================================
# 3. cover_contention：跨岗位争用同一人
# =========================================================================

def cover_contention():
    # 两岗位同刻开席，每岗一个 OPS 席 + 一个 AUDIT 席，共两个 OPS 需求。
    # carol 在 t=30 失效：第一段 alice/carol 各占一岗完整；第二段 OPS 仅 alice
    # 一人，两个 OPS 席争用同一人，最多再填一席，出现空席。
    events = [
        base_event("C1", 1, "alice", "OPS", ["TOP"], 0, 60),
        base_event("C2", 1, "carol", "OPS", ["TOP"], 0, 30),
        base_event("C3", 1, "ben", "AUDIT", ["TOP"], 0, 60),
        base_event("C4", 1, "dan", "AUDIT", ["TOP"], 0, 60),
    ]
    case = {
        "case_id": "cover_contention",
        "categories": CATEGORIES,
        "qual_events": events,
        "appointments": [],
        "cooling_rules": [],
        "positions": [
            {"position_id": "PA", "required_categories": ["TOP"],
             "seats": [{"seat_id": "A1", "department": "OPS"},
                       {"seat_id": "A2", "department": "AUDIT"}],
             "min_span": 1, "handover": None},
            {"position_id": "PB", "required_categories": ["TOP"],
             "seats": [{"seat_id": "B1", "department": "OPS"},
                       {"seat_id": "B2", "department": "AUDIT"}],
             "min_span": 1, "handover": None},
        ],
        "service_combos": [
            {"combo_id": "C1", "positions": ["PA", "PB"],
             "from_time": 0, "to_time": 30},
            {"combo_id": "C2", "positions": ["PA", "PB"],
             "from_time": 30, "to_time": 60},
        ],
        "query": {"cutoff": 60, "from_time": 0, "to_time": 60},
    }
    write("cover_contention.json", case)


# =========================================================================
# 4. cover_dual_sign_fail：同部门双签失败（第二席无独立部门持证人）
# =========================================================================

def cover_dual_sign_fail():
    events = [
        base_event("D1", 1, "alice", "OPS", ["TOP"], 0, 40),
        base_event("D2", 1, "bob", "OPS", ["TOP"], 0, 40),
        # AUDIT 部门没有任何 TOP 持证人
    ]
    case = {
        "case_id": "cover_dual_sign_fail",
        "categories": CATEGORIES,
        "qual_events": events,
        "appointments": [],
        "cooling_rules": [],
        "positions": [
            {"position_id": "VAULT", "required_categories": ["TOP"],
             "seats": [{"seat_id": "V1", "department": "OPS"},
                       {"seat_id": "V2", "department": "AUDIT"}],
             "min_span": 1, "handover": None},
        ],
        "service_combos": [
            {"combo_id": "C1", "positions": ["VAULT"],
             "from_time": 0, "to_time": 40},
        ],
        "query": {"cutoff": 40, "from_time": 0, "to_time": 40},
    }
    write("cover_dual_sign_fail.json", case)


# =========================================================================
# 5. cover_cooldown：冷却期结束恰可上岗
# =========================================================================

def cover_cooldown():
    events = [
        # heidi 是 OPS 唯一 SEC 持证人；[40,70) 冷却，70 起恢复
        base_event("K1", 1, "heidi", "OPS", ["SEC"], 0, 120),
        base_event("K2", 1, "henry", "AUDIT", ["SEC"], 0, 120),
    ]
    case = {
        "case_id": "cover_cooldown",
        "categories": CATEGORIES,
        "qual_events": events,
        "appointments": [
            {"appointment_id": "AP-1", "person": "heidi",
             "position": "CTRL", "from_time": 0, "to_time": 40},
        ],
        "cooling_rules": [
            {"rule_id": "CR-1", "controlled_position": "CTRL",
             "barred_position": "GUARD", "cooldown": 30},
        ],
        "positions": [
            {"position_id": "CTRL", "required_categories": ["SEC"],
             "seats": TWO_SEAT_GUARD, "min_span": 1, "handover": None},
            {"position_id": "GUARD", "required_categories": ["SEC"],
             "seats": TWO_SEAT_GUARD, "min_span": 1, "handover": None},
        ],
        "service_combos": [
            {"combo_id": "C1", "positions": ["GUARD"],
             "from_time": 20, "to_time": 90},
        ],
        "query": {"cutoff": 120, "from_time": 20, "to_time": 90},
    }
    write("cover_cooldown.json", case)


# =========================================================================
# 6. cover_correction_middle：更正只改变中间一段
# =========================================================================

def cover_correction_middle():
    events = [
        base_event("M1", 1, "alice", "OPS", ["TOP"], 0, 90),
        base_event("M2", 1, "bob", "AUDIT", ["TOP"], 0, 90),
        # 补录的更正在 cutoff=100 才可见；cutoff=50 的旧视图看不到
        base_event("M1", 2, "alice", "OPS", ["PUB"], 0, 90,
                   replaces=1, eff_from=30, eff_to=60, entered_at=80),
    ]
    case = {
        "case_id": "cover_correction_middle",
        "categories": CATEGORIES,
        "qual_events": events,
        "appointments": [],
        "cooling_rules": [],
        "positions": [
            {"position_id": "VAULT", "required_categories": ["TOP"],
             "seats": TWO_SEAT_GUARD, "min_span": 1, "handover": None},
        ],
        "service_combos": [
            {"combo_id": "C1", "positions": ["VAULT"],
             "from_time": 0, "to_time": 90},
        ],
        "query": {"cutoff": 100, "old_cutoff": 50, "new_cutoff": 100,
                  "from_time": 0, "to_time": 90},
    }
    write("cover_correction_middle.json", case)


# =========================================================================
# 7. cover_partial：无完整指派时输出最大合法部分指派并标空席
# =========================================================================

def cover_partial():
    events = [
        base_event("P1", 1, "alice", "OPS", ["SEC"], 0, 50),
        # AUDIT 无人持证 -> 每段两席只能填一席
    ]
    case = {
        "case_id": "cover_partial",
        "categories": CATEGORIES,
        "qual_events": events,
        "appointments": [],
        "cooling_rules": [],
        "positions": [
            {"position_id": "GUARD", "required_categories": ["SEC"],
             "seats": TWO_SEAT_GUARD, "min_span": 1, "handover": None},
        ],
        "service_combos": [
            {"combo_id": "C1", "positions": ["GUARD"],
             "from_time": 0, "to_time": 50},
        ],
        "query": {"cutoff": 50, "from_time": 0, "to_time": 50},
    }
    write("cover_partial.json", case)


def main():
    cover_demo()
    cover_hierarchy()
    cover_contention()
    cover_dual_sign_fail()
    cover_cooldown()
    cover_correction_middle()
    cover_partial()
    print("cover fixtures generated")


if __name__ == "__main__":
    main()
