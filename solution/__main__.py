"""命令行入口：python -m solution <命令> CASE.json [--db DB.sqlite]

任一命令标准输出均为 UTF-8 JSON；逻辑输入相同则输出字节级一致。
"""

from __future__ import annotations

import argparse
import json
import sys

from .compare import run_compare
from .db import PlanStore
from .errors import RuntimeToolError, ValidationFailure
from .solver_cover import solve_cover
from .solver_schedule import solve_schedule
from .timeline import EvidenceView
from .validate import load_case


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m solution",
        description="安全资质覆盖审计工具",
    )
    parser.add_argument(
        "command",
        choices=("cover", "schedule", "compare"),
        help="cover=逐段覆盖；schedule=连续排班；compare=新旧证据视图差异",
    )
    parser.add_argument("case_path", help="CASE.json 路径")
    parser.add_argument("--db", dest="db_path", default=None,
                        help="SQLite 持久化路径（schedule 发布 / compare 读取已发布计划）")
    parser.add_argument("--cutoff", type=int, default=None,
                        help="覆盖 query.cutoff（cover/schedule）")
    parser.add_argument("--old-cutoff", type=int, default=None,
                        help="覆盖 query.old_cutoff（compare）")
    parser.add_argument("--new-cutoff", type=int, default=None,
                        help="覆盖 query.new_cutoff（compare）")
    parser.add_argument("--mode", choices=("cover", "schedule"), default=None,
                        help="compare 口径；默认按持久化库自动判定")
    return parser


def _emit(payload: dict, ok: bool) -> int:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    return 0 if ok else 2


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        with open(args.case_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return _emit(
            {"ok": False, "errors": [
                {"code": "INVALID_VALUE", "field": "$.case_path",
                 "message": f"文件不存在: {args.case_path}"}]},
            False,
        )
    except json.JSONDecodeError as exc:
        return _emit(
            {"ok": False, "errors": [
                {"code": "TYPE_ERROR",
                 "field": f"${_json_path(exc)}",
                 "message": f"JSON 解析失败: {exc.msg}"}]},
            False,
        )

    try:
        case = load_case(raw)
        store = PlanStore(args.db_path) if args.db_path else None
        try:
            payload = _dispatch(args, case, store)
        finally:
            if store is not None:
                store.close()
        return _emit({"ok": True, "result": payload}, True)

    except ValidationFailure as exc:
        return _emit(
            {"ok": False,
             "errors": [e.to_dict() for e in exc.errors]},
            False,
        )
    except RuntimeToolError as exc:
        return _emit(
            {"ok": False, "errors": [exc.to_dict()]},
            False,
        )


def _json_path(exc: json.JSONDecodeError) -> str:
    # 给出行列近似位置，字段位置由结构校验进一步精确定位
    return f"[line {exc.lineno}, col {exc.colno}]"


def _dispatch(args, case, store):
    if args.command == "cover":
        cutoff = args.cutoff if args.cutoff is not None else case.query.get("cutoff")
        if cutoff is None:
            raise RuntimeToolError(
                "QUERY_MISMATCH", "cover 需要 cutoff（query 或 --cutoff）", "$.query.cutoff"
            )
        view = EvidenceView(case, cutoff)
        return solve_cover(view)

    if args.command == "schedule":
        cutoff = args.cutoff if args.cutoff is not None else case.query.get("cutoff")
        if cutoff is None:
            raise RuntimeToolError(
                "QUERY_MISMATCH", "schedule 需要 cutoff（query 或 --cutoff）",
                "$.query.cutoff",
            )
        view = EvidenceView(case, cutoff)
        result = solve_schedule(view)
        if store is not None and result.get("feasible"):
            run_id = store.publish(case.case_id, "schedule", cutoff, result)
            result = dict(result)
            result["published_run_id"] = run_id
        return result

    # compare
    return run_compare(case, args.old_cutoff, args.new_cutoff, store,
                       mode=args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
