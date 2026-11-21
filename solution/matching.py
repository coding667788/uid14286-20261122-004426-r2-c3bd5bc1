"""二分匹配工具（Kuhn 增广路），供约束搜索做可行性剪枝与最大填充估计。"""

from __future__ import annotations


def max_matching(options_by_seat: dict) -> dict:
    """席位 -> 人的最大基数匹配。

    ``options_by_seat`` 为 {seat_key: {可行人集合}}，
    返回 {seat_key: person} 的一个最大匹配（不保证字典序，仅用于基数/可行性）。
    """
    seat_keys = list(options_by_seat.keys())
    # person -> 当前占用该人的席位
    match: dict[object, object] = {}

    def augment(seat, visited: set) -> bool:
        for person in options_by_seat[seat]:
            if person in visited:
                continue
            visited.add(person)
            if person not in match or augment(match[person], visited):
                match[person] = seat
                return True
        return False

    for seat in seat_keys:
        augment(seat, set())

    return {seat: person for person, seat in match.items()}
