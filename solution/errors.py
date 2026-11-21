"""统一错误码与校验异常。

所有输入错误都带字段位置（JSONPath 风格，如 ``$.query.cutoff``）、
机器可读错误码和人类可读说明。
"""

from __future__ import annotations

from dataclasses import dataclass


# ---- 错误码常量 ---------------------------------------------------------

TYPE_ERROR = "TYPE_ERROR"                 # 类型不符
MISSING_FIELD = "MISSING_FIELD"           # 必填字段缺失
INVALID_VALUE = "INVALID_VALUE"           # 取值非法（大小关系、负数等）
DUPLICATE_ID = "DUPLICATE_ID"             # 标识重复
UNKNOWN_REFERENCE = "UNKNOWN_REFERENCE"   # 引用了不存在的实体
CYCLE_DETECTED = "CYCLE_DETECTED"         # 类别层级出现环
OVERLAPPING_CORRECTION = "OVERLAPPING_CORRECTION"  # 更正生效区间互叠
QUERY_MISMATCH = "QUERY_MISMATCH"         # 查询参数与命令不匹配
NO_PUBLISHED_PLAN = "NO_PUBLISHED_PLAN"   # compare 找不到已发布计划
SEARCH_LIMIT = "SEARCH_LIMIT"             # 搜索节点超过安全上限
SCALE_LIMIT = "SCALE_LIMIT"               # 超出题目约定的规模上限


@dataclass(frozen=True)
class ValidationError(Exception):
    """单条输入错误。"""

    code: str
    field: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - 仅调试用
        return f"{self.code} at {self.field}: {self.message}"

    def to_dict(self) -> dict:
        return {"code": self.code, "field": self.field, "message": self.message}


class ValidationFailure(Exception):
    """聚合的输入错误集合，CLI 捕获后整体输出。"""

    def __init__(self, errors: list[ValidationError]):
        assert errors, "ValidationFailure 至少包含一条错误"
        self.errors = errors
        super().__init__("; ".join(str(e) for e in errors))


class RuntimeToolError(Exception):
    """非输入类运行期错误（如缺少已发布计划、搜索上限）。"""

    def __init__(self, code: str, message: str, field: str = "$"):
        self.code = code
        self.field = field
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"code": self.code, "field": self.field, "message": self.message}
