"""已发布计划的 SQLite 持久化（仅在命令行显式 --db 时使用）。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS published_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    command TEXT NOT NULL,
    cutoff INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class PlanStore:
    def __init__(self, path: str):
        self.path = path
        # 父目录不存在时创建（:memory: 除外）
        if path != ":memory:":
            parent = Path(path).parent
            if str(parent) not in ("", "."):
                parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    def publish(self, case_id: str, command: str, cutoff: int, result: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO published_runs (case_id, command, cutoff, result_json) "
            "VALUES (?, ?, ?, ?)",
            (case_id, command, cutoff, json.dumps(result, sort_keys=True, ensure_ascii=False)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def latest(self, case_id: str, command: str):
        row = self.conn.execute(
            "SELECT id, cutoff, result_json FROM published_runs "
            "WHERE case_id = ? AND command = ? ORDER BY id DESC LIMIT 1",
            (case_id, command),
        ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row[0],
            "cutoff": row[1],
            "result": json.loads(row[2]),
        }

    def close(self) -> None:
        self.conn.close()
