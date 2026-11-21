"""领域数据模型（校验后的内部表示）。

时间一律为整数时刻，半开区间 ``[grant, terminate)`` 与
``[effective_from, effective_to)``，比较与排序都是纯整数运算，保证确定性。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QualEvent:
    """资质版本事件（授予 / 终止 / 更正）。

    每个版本由同一资质 ID + 版本号标识。``replaces`` 非 None 时为更正事件，
    它在 ``[effective_from, effective_to)`` 内替换 ``replaces`` 指向的版本。
    """

    qual_id: str
    version: int
    person: str
    department: str
    categories: tuple[str, ...]
    grant: int
    terminate: int
    replaces: int | None
    effective_from: int
    effective_to: int
    entered_at: int


@dataclass(frozen=True)
class Appointment:
    """已签任记录（冷却历史的来源，排程不生成这些历史）。"""

    appointment_id: str
    person: str
    position: str
    from_time: int
    to_time: int


@dataclass(frozen=True)
class CoolingRule:
    """冷却规则：在某岗位签任后，冷却窗口内禁止担任受限岗位。"""

    rule_id: str
    controlled_position: str
    barred_position: str
    cooldown: int


@dataclass(frozen=True)
class Position:
    """岗位：两个固定席位，分属两个不同部门。"""

    position_id: str
    required_categories: tuple[str, ...]
    seats: tuple["SeatSpec", ...]
    min_span: int
    handover_department: str | None
    handover_categories: tuple[str, ...]


@dataclass(frozen=True)
class SeatSpec:
    seat_id: str
    department: str


@dataclass(frozen=True)
class ServiceCombo:
    """服务组合：在某时段同时要求一组岗位开席。"""

    combo_id: str
    positions: tuple[str, ...]
    from_time: int
    to_time: int


@dataclass(frozen=True)
class Case:
    case_id: str
    categories: dict[str, str | None]           # 类别 -> 父类别
    events: tuple[QualEvent, ...]
    appointments: tuple[Appointment, ...]
    cooling_rules: tuple[CoolingRule, ...]
    positions: tuple[Position, ...]
    combos: tuple[ServiceCombo, ...]
    query: dict


# ---- 求解输出使用的轻量结构 ----------------------------------------------


@dataclass
class Segment:
    index: int
    start: int
    end: int
    active_positions: tuple[str, ...]


@dataclass
class SeatOption:
    """某席位在某段上的一种用人方案：人 + 最小资质集合。"""

    person: str
    qual_ids: tuple[str, ...]


@dataclass
class SegmentAssignment:
    """cover 的单段结果；空席 ``seat`` 给 None。"""

    position_id: str
    seat_id: str
    department: str
    person: str | None
    qual_ids: tuple[str, ...]


@dataclass
class ScheduleSeat:
    position_id: str
    seat_id: str
    department: str
    person: str
    qual_ids: tuple[str, ...]


@dataclass
class Handover:
    """段交界上的一次交接见证。"""

    boundary_time: int
    position_id: str
    seat_id: str
    old_person: str
    new_person: str
    witness: str
    witness_qual_ids: tuple[str, ...]
