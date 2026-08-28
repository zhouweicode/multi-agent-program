"""SQLite development storage for long-term memory facts and update jobs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from services.memory_errors import MemoryRevisionConflict


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalized_hash(content: str) -> str:
    normalized = " ".join(content.split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class SQLiteLongTermMemoryRepository:
    def __init__(self, path: str):
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._lock = Lock()
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS memory_facts (
                    fact_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    normalized_hash TEXT NOT NULL,
                    category TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_run_id TEXT,
                    source_conversation_id TEXT,
                    expected_valid_until TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    revision INTEGER NOT NULL DEFAULT 1,
                    recall_count INTEGER NOT NULL DEFAULT 0,
                    application_count INTEGER NOT NULL DEFAULT 0,
                    last_recalled_at TEXT,
                    last_applied_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, agent_name, normalized_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_facts_scope
                    ON memory_facts(user_id, agent_name, status, category, updated_at DESC);
                CREATE TABLE IF NOT EXISTS memory_update_jobs (
                    job_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL DEFAULT '',
                    conversation_id TEXT,
                    run_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, agent_name, run_id)
                );
                CREATE TABLE IF NOT EXISTS memory_audit_logs (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL DEFAULT '',
                    operation TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_audit_user
                    ON memory_audit_logs(user_id, created_at DESC);
            """)
            columns = {
                row[1] for row in self._connection.execute(
                    "PRAGMA table_info(memory_facts)"
                ).fetchall()
            }
            for name, definition in (
                ("recall_count", "INTEGER NOT NULL DEFAULT 0"),
                ("application_count", "INTEGER NOT NULL DEFAULT 0"),
                ("last_recalled_at", "TEXT"),
                ("last_applied_at", "TEXT"),
            ):
                if name not in columns:
                    self._connection.execute(
                        f"ALTER TABLE memory_facts ADD COLUMN {name} {definition}"
                    )
            self._connection.execute("""
                UPDATE memory_facts
                SET expected_valid_until=datetime(updated_at, '+90 days')
                WHERE expected_valid_until IS NULL
            """)

    @staticmethod
    def _fact(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def search(self, user_id: str, query: str, top_k: int = 5,
               agent_name: str | None = None) -> list[dict[str, Any]]:
        words = [word for word in query.strip().split() if word]
        pattern = f"%{'%'.join(words) if words else query.strip()}%"
        with self._lock:
            rows = self._connection.execute("""
                SELECT * FROM memory_facts
                WHERE user_id=? AND agent_name=? AND status='active'
                  AND (?='' OR content LIKE ?)
                ORDER BY confidence DESC, updated_at DESC, fact_id
                LIMIT ?
            """, (user_id, agent_name or "", query.strip(), pattern,
                  max(1, min(top_k, 100)))).fetchall()
        return [self._fact(row) for row in rows]

    def list_facts(self, user_id: str, limit: int = 100,
                   agent_name: str | None = None,
                   include_archived: bool = False) -> list[dict[str, Any]]:
        status_clause = "" if include_archived else "AND status='active'"
        with self._lock:
            rows = self._connection.execute(f"""
                SELECT * FROM memory_facts
                WHERE user_id=? AND agent_name=? {status_clause}
                ORDER BY updated_at DESC, fact_id LIMIT ?
            """, (user_id, agent_name or "", max(1, min(limit, 1000)))).fetchall()
        return [self._fact(row) for row in rows]

    def create(self, user_id: str, content: str, category: str = "context",
               confidence: float = 0.8, agent_name: str | None = None,
               source_run_id: str | None = None,
               source_conversation_id: str | None = None,
               expected_valid_until: str | None = None) -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError("记忆事实不能为空")
        now = _now()
        fact_id = f"fact-{uuid4().hex}"
        digest = _normalized_hash(content)
        with self._lock, self._connection:
            self._connection.execute("""
                INSERT INTO memory_facts(
                    fact_id, user_id, agent_name, content, normalized_hash,
                    category, confidence, source_run_id, source_conversation_id,
                    expected_valid_until, status, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)
                ON CONFLICT(user_id, agent_name, normalized_hash) DO UPDATE SET
                    confidence=MAX(memory_facts.confidence, excluded.confidence),
                    updated_at=excluded.updated_at
            """, (fact_id, user_id, agent_name or "", content, digest, category,
                  max(0.0, min(float(confidence), 1.0)), source_run_id,
                  source_conversation_id, expected_valid_until, now, now))
            row = self._connection.execute("""
                SELECT * FROM memory_facts
                WHERE user_id=? AND agent_name=? AND normalized_hash=?
            """, (user_id, agent_name or "", digest)).fetchone()
        return self._fact(row)

    def update(self, user_id: str, fact_id: str, changes: dict[str, Any],
               agent_name: str | None = None,
               expected_revision: int | None = None) -> dict[str, Any]:
        allowed = {
            "content", "category", "confidence", "expected_valid_until", "status",
            "source_run_id", "source_conversation_id",
        }
        updates = {key: value for key, value in changes.items() if key in allowed}
        if not updates:
            return self.get(user_id, fact_id, agent_name)
        if "content" in updates:
            updates["content"] = str(updates["content"]).strip()
            if not updates["content"]:
                raise ValueError("记忆事实不能为空")
            updates["normalized_hash"] = _normalized_hash(updates["content"])
        if "confidence" in updates:
            updates["confidence"] = max(0.0, min(float(updates["confidence"]), 1.0))
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{key}=?" for key in updates)
        values = list(updates.values())
        with self._lock, self._connection:
            revision_clause = " AND revision=?" if expected_revision is not None else ""
            cursor = self._connection.execute(
                f"""UPDATE memory_facts SET {assignments}, revision=revision+1
                    WHERE user_id=? AND agent_name=? AND fact_id=?{revision_clause}""",
                (*values, user_id, agent_name or "", fact_id,
                 *((expected_revision,) if expected_revision is not None else ())),
            )
            if not cursor.rowcount:
                row = self._connection.execute(
                    """SELECT revision FROM memory_facts
                       WHERE user_id=? AND agent_name=? AND fact_id=?""",
                    (user_id, agent_name or "", fact_id),
                ).fetchone()
                if not row:
                    raise KeyError(fact_id)
                raise MemoryRevisionConflict(
                    fact_id, int(expected_revision), int(row["revision"])
                )
        return self.get(user_id, fact_id, agent_name)

    def mark_recalled(self, user_id: str, fact_ids: list[str],
                      agent_name: str | None = None) -> int:
        if not fact_ids:
            return 0
        placeholders = ",".join("?" for _ in fact_ids)
        with self._lock, self._connection:
            cursor = self._connection.execute(f"""
                UPDATE memory_facts SET recall_count=recall_count+1,
                    last_recalled_at=?
                WHERE user_id=? AND agent_name=? AND fact_id IN ({placeholders})
            """, (_now(), user_id, agent_name or "", *fact_ids))
        return int(cursor.rowcount)

    def mark_applied(self, user_id: str, fact_ids: list[str],
                     agent_name: str | None = None) -> int:
        if not fact_ids:
            return 0
        placeholders = ",".join("?" for _ in fact_ids)
        with self._lock, self._connection:
            cursor = self._connection.execute(f"""
                UPDATE memory_facts SET application_count=application_count+1,
                    last_applied_at=?
                WHERE user_id=? AND agent_name=? AND fact_id IN ({placeholders})
            """, (_now(), user_id, agent_name or "", *fact_ids))
        return int(cursor.rowcount)

    def audit(self, user_id: str, operation: str, target_type: str,
              target_id: str | None = None, metadata: dict[str, Any] | None = None,
              agent_name: str | None = None) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute("""
                INSERT INTO memory_audit_logs(
                    user_id, agent_name, operation, target_type, target_id,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, agent_name or "", operation, target_type, target_id,
                  json.dumps(metadata or {}, ensure_ascii=False), _now()))
        return int(cursor.lastrowid)

    def list_audit_logs(self, user_id: str, limit: int = 100,
                        agent_name: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("""
                SELECT * FROM memory_audit_logs
                WHERE user_id=? AND agent_name=?
                ORDER BY audit_id DESC LIMIT ?
            """, (user_id, agent_name or "", max(1, min(limit, 500)))).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            result.append(item)
        return result

    def get(self, user_id: str, fact_id: str,
            agent_name: str | None = None) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("""
                SELECT * FROM memory_facts
                WHERE user_id=? AND agent_name=? AND fact_id=?
            """, (user_id, agent_name or "", fact_id)).fetchone()
        if not row:
            raise KeyError(fact_id)
        return self._fact(row)

    def delete(self, user_id: str, fact_id: str,
               agent_name: str | None = None,
               expected_revision: int | None = None) -> bool:
        with self._lock, self._connection:
            revision_clause = " AND revision=?" if expected_revision is not None else ""
            cursor = self._connection.execute("""
                DELETE FROM memory_facts
                WHERE user_id=? AND agent_name=? AND fact_id=?
            """ + revision_clause, (user_id, agent_name or "", fact_id,
                                     *((expected_revision,)
                                       if expected_revision is not None else ())))
            if not cursor.rowcount and expected_revision is not None:
                row = self._connection.execute(
                    """SELECT revision FROM memory_facts
                       WHERE user_id=? AND agent_name=? AND fact_id=?""",
                    (user_id, agent_name or "", fact_id),
                ).fetchone()
                if row:
                    raise MemoryRevisionConflict(
                        fact_id, expected_revision, int(row["revision"])
                    )
        return bool(cursor.rowcount)

    def clear_facts(self, user_id: str, agent_name: str | None = None) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute("""
                DELETE FROM memory_facts WHERE user_id=? AND agent_name=?
            """, (user_id, agent_name or ""))
        return int(cursor.rowcount)

    def clear(self, user_id: str, agent_name: str | None = None) -> int:
        return self.clear_facts(user_id, agent_name)

    def enqueue(self, user_id: str, run_id: str, payload: dict[str, Any],
                conversation_id: str | None = None,
                agent_name: str | None = None) -> bool:
        now = _now()
        with self._lock, self._connection:
            cursor = self._connection.execute("""
                INSERT INTO memory_update_jobs(
                    job_id, user_id, agent_name, conversation_id, run_id,
                    payload_json, status, available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                ON CONFLICT(user_id, agent_name, run_id) DO NOTHING
            """, (f"memjob-{uuid4().hex}", user_id, agent_name or "",
                  conversation_id, run_id, json.dumps(payload, ensure_ascii=False),
                  now, now, now))
        return bool(cursor.rowcount)

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        job = dict(row)
        raw = job.pop("payload_json")
        job["payload"] = json.loads(raw) if isinstance(raw, str) else raw
        return job

    def claim_jobs(self, limit: int = 10, lease_seconds: int = 60) -> list[dict[str, Any]]:
        """Atomically lease pending/retry jobs and reclaim expired workers."""
        now = _now()
        lease_until = (datetime.now(UTC) + timedelta(
            seconds=max(5, lease_seconds)
        )).isoformat()
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                rows = self._connection.execute("""
                    SELECT * FROM memory_update_jobs
                    WHERE available_at <= ? AND status IN ('pending', 'retry', 'processing')
                    ORDER BY available_at, created_at, job_id
                    LIMIT ?
                """, (now, max(1, min(limit, 100)))).fetchall()
                for row in rows:
                    self._connection.execute("""
                        UPDATE memory_update_jobs
                        SET status='processing', attempt_count=attempt_count+1,
                            available_at=?, error=NULL, updated_at=?
                        WHERE job_id=?
                    """, (lease_until, now, row["job_id"]))
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
            claimed = []
            for row in rows:
                item = self._job(row)
                item.update({
                    "status": "processing",
                    "attempt_count": int(row["attempt_count"]) + 1,
                    "available_at": lease_until,
                    "error": None,
                    "updated_at": now,
                })
                claimed.append(item)
        return claimed

    def complete_job(self, job_id: str) -> bool:
        now = _now()
        with self._lock, self._connection:
            cursor = self._connection.execute("""
                UPDATE memory_update_jobs
                SET status='completed', error=NULL, updated_at=?
                WHERE job_id=? AND status='processing'
            """, (now, job_id))
        return bool(cursor.rowcount)

    def fail_job(self, job_id: str, error: str, retry_after_seconds: int,
                 terminal: bool = False) -> bool:
        now = datetime.now(UTC)
        available_at = (now + timedelta(
            seconds=max(0, retry_after_seconds)
        )).isoformat()
        with self._lock, self._connection:
            cursor = self._connection.execute("""
                UPDATE memory_update_jobs
                SET status=?, available_at=?, error=?, updated_at=?
                WHERE job_id=? AND status='processing'
            """, ('failed' if terminal else 'retry', available_at,
                  str(error)[:2000], now.isoformat(), job_id))
        return bool(cursor.rowcount)

    def job_stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._connection.execute("""
                SELECT status, COUNT(*) AS count FROM memory_update_jobs GROUP BY status
            """).fetchall()
        stats = {"pending": 0, "processing": 0, "retry": 0,
                 "completed": 0, "failed": 0}
        stats.update({str(row["status"]): int(row["count"]) for row in rows})
        return stats

    def clear_update_jobs(self, user_id: str) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM memory_update_jobs WHERE user_id=?", (user_id,)
            )
        return int(cursor.rowcount)

    def close(self) -> None:
        with self._lock:
            self._connection.close()
