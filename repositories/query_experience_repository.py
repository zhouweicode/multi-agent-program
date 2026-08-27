"""Durable query experience events and aggregated strategy patterns."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteQueryExperienceRepository:
    def __init__(self, path: str):
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._lock = Lock()
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA busy_timeout=30000")
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS query_experience_patterns (
                    pattern_id TEXT PRIMARY KEY,
                    scope_id TEXT NOT NULL,
                    query_template TEXT NOT NULL,
                    strategy_json TEXT NOT NULL,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    average_quality REAL NOT NULL DEFAULT 0,
                    average_duration_ms REAL NOT NULL DEFAULT 0,
                    average_tokens REAL NOT NULL DEFAULT 0,
                    average_cost REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(scope_id, query_template)
                );
                CREATE INDEX IF NOT EXISTS idx_experience_patterns_scope
                    ON query_experience_patterns(scope_id, success_count DESC, updated_at DESC);
                CREATE TABLE IF NOT EXISTS query_experience_events (
                    run_id TEXT PRIMARY KEY,
                    pattern_id TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    normalized_question TEXT NOT NULL,
                    query_template TEXT NOT NULL,
                    strategy_json TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    eligible INTEGER NOT NULL,
                    validation_pass INTEGER NOT NULL,
                    quality_score REAL NOT NULL,
                    duration_ms REAL,
                    total_tokens INTEGER,
                    cost REAL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(pattern_id) REFERENCES query_experience_patterns(pattern_id)
                );
                CREATE INDEX IF NOT EXISTS idx_experience_events_pattern
                    ON query_experience_events(pattern_id, created_at DESC);
            """)

    @staticmethod
    def _decode_pattern(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["strategy"] = json.loads(result.pop("strategy_json") or "{}")
        result["success_rate"] = round(
            result["success_count"] / result["sample_count"], 4
        ) if result["sample_count"] else 0.0
        return result

    def record(self, event: dict[str, Any]) -> bool:
        """Record one terminal run. Returns False when the run was already written."""
        now = _now()
        with self._lock, self._connection:
            exists = self._connection.execute(
                "SELECT 1 FROM query_experience_events WHERE run_id=?", (event["run_id"],)
            ).fetchone()
            if exists:
                return False
            pattern = self._connection.execute(
                "SELECT * FROM query_experience_patterns WHERE pattern_id=?", (event["pattern_id"],)
            ).fetchone()
            sample_count = int(pattern["sample_count"]) if pattern else 0
            old_quality = float(pattern["average_quality"]) if pattern else 0.0
            new_count = sample_count + 1
            average_quality = (old_quality * sample_count + float(event["quality_score"])) / new_count
            if pattern:
                # Keep the most recent successful strategy. Failed executions are
                # valuable negative evidence but must not become recommendations.
                strategy_json = (json.dumps(event["strategy"], ensure_ascii=False)
                                 if event["eligible"] else pattern["strategy_json"])
                self._connection.execute("""
                    UPDATE query_experience_patterns SET
                        strategy_json=?, sample_count=?,
                        success_count=success_count + ?, failure_count=failure_count + ?,
                        average_quality=?, updated_at=?
                    WHERE pattern_id=?
                """, (strategy_json, new_count, int(event["eligible"]), int(not event["eligible"]),
                      round(average_quality, 6), now, event["pattern_id"]))
            else:
                self._connection.execute("""
                    INSERT INTO query_experience_patterns(
                        pattern_id, scope_id, query_template, strategy_json, sample_count,
                        success_count, failure_count, average_quality, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """, (event["pattern_id"], event["scope_id"], event["query_template"],
                      json.dumps(event["strategy"], ensure_ascii=False), int(event["eligible"]),
                      int(not event["eligible"]), round(float(event["quality_score"]), 6), now, now))
            self._connection.execute("""
                INSERT INTO query_experience_events(
                    run_id, pattern_id, scope_id, normalized_question, query_template,
                    strategy_json, outcome, eligible, validation_pass, quality_score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (event["run_id"], event["pattern_id"], event["scope_id"],
                  event["normalized_question"], event["query_template"],
                  json.dumps(event["strategy"], ensure_ascii=False), event["outcome"],
                  int(event["eligible"]), int(event["validation_pass"]),
                  round(float(event["quality_score"]), 6), now))
        return True

    def finalize_metrics(self, run_id: str, metrics: dict[str, Any]) -> bool:
        with self._lock, self._connection:
            event = self._connection.execute(
                "SELECT pattern_id FROM query_experience_events WHERE run_id=?", (run_id,)
            ).fetchone()
            if not event:
                return False
            self._connection.execute("""
                UPDATE query_experience_events SET duration_ms=?, total_tokens=?, cost=? WHERE run_id=?
            """, (float(metrics.get("duration_ms") or 0), int(metrics.get("total_tokens") or 0),
                  float(metrics.get("cost") or 0), run_id))
            aggregates = self._connection.execute("""
                SELECT AVG(COALESCE(duration_ms, 0)) AS duration_ms,
                       AVG(COALESCE(total_tokens, 0)) AS tokens,
                       AVG(COALESCE(cost, 0)) AS cost
                FROM query_experience_events WHERE pattern_id=?
            """, (event["pattern_id"],)).fetchone()
            self._connection.execute("""
                UPDATE query_experience_patterns SET average_duration_ms=?, average_tokens=?,
                    average_cost=?, updated_at=? WHERE pattern_id=?
            """, (round(float(aggregates["duration_ms"] or 0), 3),
                  round(float(aggregates["tokens"] or 0), 3),
                  round(float(aggregates["cost"] or 0), 8), _now(), event["pattern_id"]))
        return True

    def list_patterns(self, scope_id: str, limit: int = 100, positive_only: bool = False) -> list[dict[str, Any]]:
        clause = "AND success_count > 0" if positive_only else ""
        with self._lock:
            rows = self._connection.execute(f"""
                SELECT * FROM query_experience_patterns
                WHERE scope_id=? {clause}
                ORDER BY success_count DESC, updated_at DESC LIMIT ?
            """, (scope_id, max(1, min(limit, 500)))).fetchall()
        return [self._decode_pattern(row) for row in rows]

    def get_pattern(self, pattern_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM query_experience_patterns WHERE pattern_id=?", (pattern_id,)
            ).fetchone()
        return self._decode_pattern(row) if row else None

    def stats(self, scope_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("""
                SELECT COUNT(*) AS pattern_count, COALESCE(SUM(sample_count), 0) AS event_count,
                       COALESCE(SUM(success_count), 0) AS success_count,
                       COALESCE(SUM(failure_count), 0) AS failure_count,
                       COALESCE(AVG(average_quality), 0) AS average_quality,
                       COALESCE(AVG(average_duration_ms), 0) AS average_duration_ms,
                       COALESCE(SUM(average_cost * sample_count), 0) AS total_cost
                FROM query_experience_patterns WHERE scope_id=?
            """, (scope_id,)).fetchone()
        return {key: (round(float(row[key]), 6) if key in {
                    "average_quality", "average_duration_ms", "total_cost"} else int(row[key]))
                for key in row.keys()}

    def close(self) -> None:
        with self._lock:
            self._connection.close()
