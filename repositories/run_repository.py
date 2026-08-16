"""SQLite Run Registry：持久保存运行状态、错误与基础耗时指标。"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class SQLiteRunRepository:
    def __init__(self, path: str):
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = Lock()
        with self._connection:
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS graph_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_json TEXT,
                    metrics_json TEXT,
                    interrupt_json TEXT
                )
            """)
            columns = {row[1] for row in self._connection.execute("PRAGMA table_info(graph_runs)")}
            if "interrupt_json" not in columns:
                self._connection.execute("ALTER TABLE graph_runs ADD COLUMN interrupt_json TEXT")

    def upsert(self, record: dict[str, Any]) -> None:
        with self._lock, self._connection:
            self._connection.execute("""
                INSERT INTO graph_runs(run_id, status, created_at, updated_at, error_json, metrics_json, interrupt_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status, updated_at=excluded.updated_at,
                    error_json=excluded.error_json, metrics_json=excluded.metrics_json,
                    interrupt_json=excluded.interrupt_json
            """, (record["run_id"], record["status"], record["created_at"], record["updated_at"],
                  json.dumps(record.get("error"), ensure_ascii=False),
                  json.dumps(record.get("metrics", {}), ensure_ascii=False),
                  json.dumps(record.get("interrupt"), ensure_ascii=False)))

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM graph_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        return {"run_id": row["run_id"], "status": row["status"], "created_at": row["created_at"],
                "updated_at": row["updated_at"], "error": json.loads(row["error_json"] or "null"),
                "metrics": json.loads(row["metrics_json"] or "{}"),
                "interrupt": json.loads(row["interrupt_json"] or "null"), "persisted": True}

    def exists(self, run_id: str) -> bool:
        return self.get(run_id) is not None

    def recover_incomplete(self) -> int:
        """进程重启后不能伪装仍在执行；将失去 Worker 的 Run 明确标记为失败。"""
        now = datetime.now(timezone.utc).isoformat()
        error = json.dumps({"type": "ProcessRestarted", "message": "服务重启导致后台任务中断"}, ensure_ascii=False)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE graph_runs SET status='FAILED', updated_at=?, error_json=? WHERE status IN ('RUNNING','CANCELLING')",
                (now, error),
            )
            return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            self._connection.close()
