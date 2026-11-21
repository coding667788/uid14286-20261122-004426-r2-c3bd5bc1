"""CASE.json 结构校验。

校验尽量一次性收集全部错误再抛出 :class:`ValidationFailure`，
每条错误带 JSONPath 风格字段位置与错误码。
"""

from __future__ import annotations

from .errors import (
    CYCLE_DETECTED,
    DUPLICATE_ID,
    INVALID_VALUE,
    MISSING_FIELD,
    OVERLAPPING_CORRECTION,
    TYPE_ERROR,
    UNKNOWN_REFERENCE,
    ValidationError,
    ValidationFailure,
)
from .model import Appointment, Case, CoolingRule, Position, QualEvent, SeatSpec, ServiceCombo


# 题目约定的规模上限（cover 口径；连续排班另有更紧口径，在 solver 内校验）
MAX_PERSONS_COVER = 50
MAX_POSITIONS = 15


def _is_int(v) -> bool:
    # bool 是 int 的子类，时刻/版本号不接受布尔
    return isinstance(v, int) and not isinstance(v, bool)


def _is_nonempty_str(v) -> bool:
    return isinstance(v, str) and len(v) > 0


def load_case(raw: object) -> Case:
    errors: list[ValidationError] = []

    if not isinstance(raw, dict):
        raise ValidationFailure(
            [ValidationError(TYPE_ERROR, "$", "根节点必须是 JSON 对象")]
        )

    case_id = _require_str(raw, "case_id", errors)

    categories = _load_categories(raw, errors)
    events = _load_events(raw, errors, categories)
    appointments = _load_appointments(raw, errors)
    cooling_rules = _load_cooling(raw, errors)
    positions = _load_positions(raw, errors, categories)
    combos = _load_combos(raw, errors, positions)
    query = _load_query(raw, errors)

    # 跨实体引用与整体性检查
    _cross_checks(
        errors, events, appointments, cooling_rules, positions, combos, categories
    )

    if errors:
        raise ValidationFailure(errors)

    return Case(
        case_id=case_id,
        categories=categories,
        events=tuple(events),
        appointments=tuple(appointments),
        cooling_rules=tuple(cooling_rules),
        positions=tuple(positions),
        combos=tuple(combos),
        query=query,
    )


# ---- 字段辅助 -----------------------------------------------------------


def _require_str(raw: dict, key: str, errors: list[ValidationError], path: str = "$") -> str:
    if key not in raw:
        errors.append(ValidationError(MISSING_FIELD, f"{path}.{key}", f"缺少必填字段 {key}"))
        return ""
    v = raw[key]
    if not _is_nonempty_str(v):
        errors.append(ValidationError(TYPE_ERROR, f"{path}.{key}", f"{key} 必须是非空字符串"))
        return ""
    return v


def _require_int(
    raw: dict, key: str, errors: list[ValidationError], path: str, minimum: int | None = 0
) -> int | None:
    if key not in raw:
        errors.append(ValidationError(MISSING_FIELD, f"{path}.{key}", f"缺少必填字段 {key}"))
        return None
    v = raw[key]
    if not _is_int(v):
        errors.append(ValidationError(TYPE_ERROR, f"{path}.{key}", f"{key} 必须是整数"))
        return None
    if minimum is not None and v < minimum:
        errors.append(ValidationError(INVALID_VALUE, f"{path}.{key}", f"{key} 必须 >= {minimum}"))
    return v


# ---- 各实体 -------------------------------------------------------------


def _load_categories(raw: dict, errors: list[ValidationError]) -> dict[str, str | None]:
    path = "$.categories"
    out: dict[str, str | None] = {}
    if "categories" not in raw:
        errors.append(ValidationError(MISSING_FIELD, path, "缺少必填字段 categories"))
        return out
    node = raw["categories"]
    if not isinstance(node, list):
        errors.append(ValidationError(TYPE_ERROR, path, "categories 必须是数组"))
        return out
    for i, item in enumerate(node):
        p = f"{path}[{i}]"
        if not isinstance(item, dict):
            errors.append(ValidationError(TYPE_ERROR, p, "类别项必须是对象"))
            continue
        cid = _require_str(item, "id", errors, p)
        parent = None
        if "parent" not in item:
            errors.append(ValidationError(MISSING_FIELD, f"{p}.parent", "顶级类别也要显式给 null"))
        else:
            pv = item["parent"]
            if pv is not None and not _is_nonempty_str(pv):
                errors.append(ValidationError(TYPE_ERROR, f"{p}.parent", "parent 必须是字符串或 null"))
            else:
                parent = pv
        if cid:
            if cid in out:
                errors.append(ValidationError(DUPLICATE_ID, f"{p}.id", f"类别 {cid} 重复"))
            out[cid] = parent
    return out


def _load_events(raw: dict, errors, categories: dict) -> list[QualEvent]:
    path = "$.qual_events"
    out: list[QualEvent] = []
    if "qual_events" not in raw:
        errors.append(ValidationError(MISSING_FIELD, path, "缺少必填字段 qual_events"))
        return out
    node = raw["qual_events"]
    if not isinstance(node, list):
        errors.append(ValidationError(TYPE_ERROR, path, "qual_events 必须是数组"))
        return out
    seen: set[tuple[str, int]] = set()
    for i, item in enumerate(node):
        p = f"{path}[{i}]"
        if not isinstance(item, dict):
            errors.append(ValidationError(TYPE_ERROR, p, "资质事件必须是对象"))
            continue
        qid = _require_str(item, "qual_id", errors, p)
        version = _require_int(item, "version", errors, p, minimum=1)
        person = _require_str(item, "person", errors, p)
        dept = _require_str(item, "department", errors, p)
        grant = _require_int(item, "grant", errors, p)
        terminate = _require_int(item, "terminate", errors, p)
        entered_at = _require_int(item, "entered_at", errors, p)
        cats = _require_str_list(item, "categories", errors, p, allow_empty=False)
        replaces = None
        if "replaces" in item and item["replaces"] is not None:
            if not _is_int(item["replaces"]) or isinstance(item["replaces"], bool) or item["replaces"] < 1:
                errors.append(
                    ValidationError(TYPE_ERROR, f"{p}.replaces", "replaces 必须是正整数或 null")
                )
            else:
                replaces = item["replaces"]
        eff_from = item.get("effective_from")
        eff_to = item.get("effective_to")
        if replaces is not None:
            if not _is_int(eff_from) or isinstance(eff_from, bool) or eff_from < 0:
                errors.append(
                    ValidationError(
                        INVALID_VALUE if eff_from is not None else MISSING_FIELD,
                        f"{p}.effective_from",
                        "更正事件必须给非负整数 effective_from",
                    )
                )
                eff_from = 0
            if not _is_int(eff_to) or isinstance(eff_to, bool) or eff_to < 0:
                errors.append(
                    ValidationError(
                        INVALID_VALUE if eff_to is not None else MISSING_FIELD,
                        f"{p}.effective_to",
                        "更正事件必须给非负整数 effective_to",
                    )
                )
                eff_to = 0
            if _is_int(eff_from) and _is_int(eff_to) and eff_from >= eff_to:
                errors.append(
                    ValidationError(
                        INVALID_VALUE, p, "effective_from 必须严格小于 effective_to"
                    )
                )
        else:
            eff_from, eff_to = 0, 0
        if grant is not None and terminate is not None and grant >= terminate:
            errors.append(
                ValidationError(INVALID_VALUE, p, "grant 必须严格小于 terminate")
            )
        for c in cats:
            if c not in categories:
                errors.append(
                    ValidationError(UNKNOWN_REFERENCE, f"{p}.categories", f"未知类别 {c}")
                )
        key = (qid, version)
        if qid and version is not None:
            if key in seen:
                errors.append(
                    ValidationError(DUPLICATE_ID, p, f"资质版本 {qid}#v{version} 重复")
                )
            seen.add(key)
        if not errors or all(
            v is not None for v in (version, grant, terminate, entered_at)
        ) and qid and person and dept and cats:
            out.append(
                QualEvent(
                    qual_id=qid,
                    version=version if version is not None else 0,
                    person=person,
                    department=dept,
                    categories=tuple(cats),
                    grant=grant if grant is not None else 0,
                    terminate=terminate if terminate is not None else 0,
                    replaces=replaces,
                    effective_from=eff_from,
                    effective_to=eff_to,
                    entered_at=entered_at if entered_at is not None else 0,
                )
            )
    return out


def _require_str_list(
    item: dict, key: str, errors, p: str, allow_empty: bool
) -> list[str]:
    if key not in item:
        errors.append(ValidationError(MISSING_FIELD, f"{p}.{key}", f"缺少必填字段 {key}"))
        return []
    node = item[key]
    if not isinstance(node, list) or not all(_is_nonempty_str(x) for x in node):
        errors.append(ValidationError(TYPE_ERROR, f"{p}.{key}", f"{key} 必须是非空字符串数组"))
        return []
    if not allow_empty and not node:
        errors.append(ValidationError(INVALID_VALUE, f"{p}.{key}", f"{key} 不能为空数组"))
    return list(node)


def _load_appointments(raw: dict, errors) -> list[Appointment]:
    path = "$.appointments"
    out: list[Appointment] = []
    if "appointments" not in raw:
        errors.append(ValidationError(MISSING_FIELD, path, "缺少必填字段 appointments（可为空数组）"))
        return out
    node = raw["appointments"]
    if not isinstance(node, list):
        errors.append(ValidationError(TYPE_ERROR, path, "appointments 必须是数组"))
        return out
    seen: set[str] = set()
    for i, item in enumerate(node):
        p = f"{path}[{i}]"
        if not isinstance(item, dict):
            errors.append(ValidationError(TYPE_ERROR, p, "签任记录必须是对象"))
            continue
        aid = _require_str(item, "appointment_id", errors, p)
        person = _require_str(item, "person", errors, p)
        position = _require_str(item, "position", errors, p)
        t0 = _require_int(item, "from_time", errors, p)
        t1 = _require_int(item, "to_time", errors, p)
        if t0 is not None and t1 is not None and t0 >= t1:
            errors.append(ValidationError(INVALID_VALUE, p, "from_time 必须严格小于 to_time"))
        if aid:
            if aid in seen:
                errors.append(ValidationError(DUPLICATE_ID, f"{p}.appointment_id", f"签任 {aid} 重复"))
            seen.add(aid)
        if aid and person and position and t0 is not None and t1 is not None:
            out.append(Appointment(aid, person, position, t0, t1))
    return out


def _load_cooling(raw: dict, errors) -> list[CoolingRule]:
    path = "$.cooling_rules"
    out: list[CoolingRule] = []
    if "cooling_rules" not in raw:
        errors.append(ValidationError(MISSING_FIELD, path, "缺少必填字段 cooling_rules（可为空数组）"))
        return out
    node = raw["cooling_rules"]
    if not isinstance(node, list):
        errors.append(ValidationError(TYPE_ERROR, path, "cooling_rules 必须是数组"))
        return out
    seen: set[str] = set()
    for i, item in enumerate(node):
        p = f"{path}[{i}]"
        if not isinstance(item, dict):
            errors.append(ValidationError(TYPE_ERROR, p, "冷却规则必须是对象"))
            continue
        rid = _require_str(item, "rule_id", errors, p)
        controlled = _require_str(item, "controlled_position", errors, p)
        barred = _require_str(item, "barred_position", errors, p)
        cooldown = _require_int(item, "cooldown", errors, p)
        if rid:
            if rid in seen:
                errors.append(ValidationError(DUPLICATE_ID, f"{p}.rule_id", f"规则 {rid} 重复"))
            seen.add(rid)
        if rid and controlled and barred and cooldown is not None:
            out.append(CoolingRule(rid, controlled, barred, cooldown))
    return out


def _load_positions(raw: dict, errors, categories: dict) -> list[Position]:
    path = "$.positions"
    out: list[Position] = []
    if "positions" not in raw:
        errors.append(ValidationError(MISSING_FIELD, path, "缺少必填字段 positions"))
        return out
    node = raw["positions"]
    if not isinstance(node, list):
        errors.append(ValidationError(TYPE_ERROR, path, "positions 必须是数组"))
        return out
    seen: set[str] = set()
    for i, item in enumerate(node):
        p = f"{path}[{i}]"
        if not isinstance(item, dict):
            errors.append(ValidationError(TYPE_ERROR, p, "岗位必须是对象"))
            continue
        pid = _require_str(item, "position_id", errors, p)
        req = _require_str_list(item, "required_categories", errors, p, allow_empty=False)
        for c in req:
            if c not in categories:
                errors.append(
                    ValidationError(UNKNOWN_REFERENCE, f"{p}.required_categories", f"未知类别 {c}")
                )
        seats = _load_seats(item, errors, p)
        min_span = 1
        if "min_span" in item:
            mv = item["min_span"]
            if not _is_int(mv) or mv < 1:
                errors.append(ValidationError(INVALID_VALUE, f"{p}.min_span", "min_span 必须是 >=1 的整数"))
            else:
                min_span = mv
        handover_dept, handover_cats = _load_handover(item, errors, p, categories, seats)
        if pid:
            if pid in seen:
                errors.append(ValidationError(DUPLICATE_ID, f"{p}.position_id", f"岗位 {pid} 重复"))
            seen.add(pid)
        if pid and req and seats:
            out.append(
                Position(
                    position_id=pid,
                    required_categories=tuple(req),
                    seats=tuple(seats),
                    min_span=min_span,
                    handover_department=handover_dept,
                    handover_categories=tuple(handover_cats),
                )
            )
    return out


def _load_seats(item: dict, errors, p: str) -> list[SeatSpec]:
    if "seats" not in item:
        errors.append(ValidationError(MISSING_FIELD, f"{p}.seats", "岗位必须声明两个席位"))
        return []
    node = item["seats"]
    if not isinstance(node, list):
        errors.append(ValidationError(TYPE_ERROR, f"{p}.seats", "seats 必须是数组"))
        return []
    if len(node) != 2:
        errors.append(ValidationError(INVALID_VALUE, f"{p}.seats", "每个岗位必须恰好两个席位（双签）"))
    seats: list[SeatSpec] = []
    seen_ids: set[str] = set()
    seen_depts: set[str] = set()
    for j, s in enumerate(node):
        sp = f"{p}.seats[{j}]"
        if not isinstance(s, dict):
            errors.append(ValidationError(TYPE_ERROR, sp, "席位必须是对象"))
            continue
        sid = _require_str(s, "seat_id", errors, sp)
        dept = _require_str(s, "department", errors, sp)
        if sid:
            if sid in seen_ids:
                errors.append(ValidationError(DUPLICATE_ID, f"{sp}.seat_id", f"席位 {sid} 重复"))
            seen_ids.add(sid)
        if dept:
            if dept in seen_depts:
                errors.append(
                    ValidationError(INVALID_VALUE, f"{sp}.department", "同岗两席必须来自不同部门")
                )
            seen_depts.add(dept)
        if sid and dept:
            seats.append(SeatSpec(sid, dept))
    return seats


def _load_handover(item: dict, errors, p: str, categories: dict, seats: list[SeatSpec]):
    hv = item.get("handover")
    if hv is None:
        return None, ()
    if not isinstance(hv, dict):
        errors.append(ValidationError(TYPE_ERROR, f"{p}.handover", "handover 必须是对象或 null"))
        return None, ()
    hp = f"{p}.handover"
    dept = _require_str(hv, "department", errors, hp)
    cats = _require_str_list(hv, "categories", errors, hp, allow_empty=False)
    for c in cats:
        if c not in categories:
            errors.append(ValidationError(UNKNOWN_REFERENCE, f"{hp}.categories", f"未知类别 {c}"))
    seat_depts = {s.department for s in seats}
    if dept and dept in seat_depts:
        errors.append(
            ValidationError(
                INVALID_VALUE, f"{hp}.department", "见证部门必须独立于该岗位两个席位部门"
            )
        )
    return dept, cats


def _load_combos(raw: dict, errors, positions: list[Position]) -> list[ServiceCombo]:
    path = "$.service_combos"
    out: list[ServiceCombo] = []
    if "service_combos" not in raw:
        errors.append(ValidationError(MISSING_FIELD, path, "缺少必填字段 service_combos"))
        return out
    node = raw["service_combos"]
    if not isinstance(node, list):
        errors.append(ValidationError(TYPE_ERROR, path, "service_combos 必须是数组"))
        return out
    pids = {p.position_id for p in positions}
    seen: set[str] = set()
    for i, item in enumerate(node):
        p = f"{path}[{i}]"
        if not isinstance(item, dict):
            errors.append(ValidationError(TYPE_ERROR, p, "服务组合必须是对象"))
            continue
        cid = _require_str(item, "combo_id", errors, p)
        plist = _require_str_list(item, "positions", errors, p, allow_empty=False)
        t0 = _require_int(item, "from_time", errors, p)
        t1 = _require_int(item, "to_time", errors, p)
        if t0 is not None and t1 is not None and t0 >= t1:
            errors.append(ValidationError(INVALID_VALUE, p, "from_time 必须严格小于 to_time"))
        for ref in plist:
            if ref not in pids:
                errors.append(
                    ValidationError(UNKNOWN_REFERENCE, f"{p}.positions", f"未知岗位 {ref}")
                )
        if cid:
            if cid in seen:
                errors.append(ValidationError(DUPLICATE_ID, f"{p}.combo_id", f"服务组合 {cid} 重复"))
            seen.add(cid)
        if cid and plist and t0 is not None and t1 is not None:
            out.append(ServiceCombo(cid, tuple(plist), t0, t1))
    return out


def _load_query(raw: dict, errors) -> dict:
    path = "$.query"
    if "query" not in raw:
        errors.append(ValidationError(MISSING_FIELD, path, "缺少必填字段 query"))
        return {}
    node = raw["query"]
    if not isinstance(node, dict):
        errors.append(ValidationError(TYPE_ERROR, path, "query 必须是对象"))
        return {}
    out: dict = {}
    for key in ("cutoff", "old_cutoff", "new_cutoff", "from_time", "to_time"):
        if key in node:
            v = node[key]
            if not _is_int(v) or v < 0:
                errors.append(ValidationError(TYPE_ERROR, f"{path}.{key}", f"{key} 必须是非负整数"))
            else:
                out[key] = v
    if "from_time" in out and "to_time" in out and out["from_time"] >= out["to_time"]:
        errors.append(
            ValidationError(INVALID_VALUE, path, "query.from_time 必须严格小于 query.to_time")
        )
    if "old_cutoff" in out and "new_cutoff" in out and out["new_cutoff"] < out["old_cutoff"]:
        errors.append(
            ValidationError(INVALID_VALUE, path, "new_cutoff 不能小于 old_cutoff")
        )
    return out


# ---- 跨实体检查 ----------------------------------------------------------


def _cross_checks(errors, events, appointments, cooling_rules, positions, combos, categories):
    pids = {p.position_id for p in positions}

    # 类别层级环
    for cid, parent in categories.items():
        seen = {cid}
        cur = parent
        while cur is not None:
            if cur not in categories:
                errors.append(
                    ValidationError(
                        UNKNOWN_REFERENCE,
                        "$.categories",
                        f"类别 {cid} 的父类别 {cur} 不存在",
                    )
                )
                break
            if cur in seen:
                errors.append(
                    ValidationError(CYCLE_DETECTED, "$.categories", f"类别层级在 {cur} 处成环")
                )
                break
            seen.add(cur)
            cur = categories[cur]

    # 资质更正引用
    bases: dict[tuple[str, int], QualEvent] = {}
    for ev in events:
        if ev.replaces is None:
            bases[(ev.qual_id, ev.version)] = ev
    for ev in events:
        if ev.replaces is not None:
            target = bases.get((ev.qual_id, ev.replaces))
            if target is None:
                errors.append(
                    ValidationError(
                        UNKNOWN_REFERENCE,
                        "$.qual_events",
                        f"更正 {ev.qual_id}#v{ev.version} 替换的基础版本 v{ev.replaces} 不存在",
                    )
                )
            if ev.replaces == ev.version:
                errors.append(
                    ValidationError(
                        INVALID_VALUE,
                        "$.qual_events",
                        f"更正 {ev.qual_id}#v{ev.version} 不能替换自身",
                    )
                )

    # 同一被替换版本的更正生效区间不得互叠
    groups: dict[tuple[str, int], list[QualEvent]] = {}
    for ev in events:
        if ev.replaces is not None:
            groups.setdefault((ev.qual_id, ev.replaces), []).append(ev)
    for key, group in groups.items():
        group_sorted = sorted(group, key=lambda e: (e.effective_from, e.effective_to))
        for a, b in zip(group_sorted, group_sorted[1:]):
            if a.effective_to > b.effective_from:
                errors.append(
                    ValidationError(
                        OVERLAPPING_CORRECTION,
                        "$.qual_events",
                        f"资质 {key[0]} 对 v{key[1]} 的更正区间 "
                        f"[{a.effective_from},{a.effective_to}) 与 "
                        f"[{b.effective_from},{b.effective_to}) 互叠",
                    )
                )

    for ap in appointments:
        if ap.position not in pids:
            errors.append(
                ValidationError(
                    UNKNOWN_REFERENCE,
                    "$.appointments",
                    f"签任 {ap.appointment_id} 引用未知岗位 {ap.position}",
                )
            )
    for rule in cooling_rules:
        for key in ("controlled_position", "barred_position"):
            v = getattr(rule, key)
            if v not in pids:
                errors.append(
                    ValidationError(
                        UNKNOWN_REFERENCE,
                        "$.cooling_rules",
                        f"规则 {rule.rule_id} 引用未知岗位 {v}",
                    )
                )

    # 规模上限（cover 口径）
    persons = {ev.person for ev in events}
    if len(persons) > MAX_PERSONS_COVER:
        errors.append(
            ValidationError(
                SCALE_LIMIT,
                "$.qual_events",
                f"持证人 {len(persons)} 人，超过 cover 上限 {MAX_PERSONS_COVER}",
            )
        )
    if len(positions) > MAX_POSITIONS:
        errors.append(
            ValidationError(
                SCALE_LIMIT,
                "$.positions",
                f"岗位 {len(positions)} 个，超过上限 {MAX_POSITIONS}",
            )
        )
