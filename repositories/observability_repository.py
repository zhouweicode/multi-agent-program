"""SQLite observability store for durable traces, spans, metrics and run comparison."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any


class SQLiteObservabilityRepository:
    def __init__(self, path: str):
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._lock = Lock()
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA busy_timeout=30000")
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS trace_runs (
                    run_id TEXT NOT NULL,
                    attempt_id INTEGER NOT NULL,
                    trace_id TEXT NOT NULL UNIQUE,
                    root_span_id TEXT NOT NULL,
                    parent_trace_id TEXT,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_ms REAL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    cost REAL NOT NULL DEFAULT 0,
                    cost_currency TEXT NOT NULL DEFAULT 'USD',
                    tool_calls INTEGER NOT NULL DEFAULT 0,
                    tool_successes INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    replan_count INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(run_id, attempt_id)
                )
            """)
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS trace_spans (
                    span_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    attempt_id INTEGER NOT NULL,
                    parent_span_id TEXT,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    duration_ms REAL NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    cost REAL NOT NULL DEFAULT 0,
                    error_type TEXT,
                    attributes_json TEXT NOT NULL DEFAULT '{}'
                )
            """)
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_trace_spans_run ON trace_spans(run_id, attempt_id, started_at)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_trace_runs_started ON trace_runs(started_at DESC)"
            )

    def start_trace(self, record: dict[str, Any]) -> None:
        with self._lock, self._connection:
            self._connection.execute("""
                INSERT INTO trace_runs(
                    run_id, attempt_id, trace_id, root_span_id, parent_trace_id,
                    status, started_at, cost_currency, metadata_json
                ) VALUES (?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?)
            """, (
                record["run_id"], record["attempt_id"], record["trace_id"], record["root_span_id"],
                record.get("parent_trace_id"), record["started_at"], record.get("cost_currency", "USD"),
                json.dumps(record.get("metadata", {}), ensure_ascii=False),
            ))

    def next_attempt(self, run_id: str) -> tuple[int, str | None]:
        with self._lock:
            row = self._connection.execute(
                "SELECT attempt_id, trace_id FROM trace_runs WHERE run_id=? ORDER BY attempt_id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return ((int(row["attempt_id"]) + 1, row["trace_id"]) if row else (1, None))

    def add_span(self, span: dict[str, Any]) -> None:
        with self._lock, self._connection:
            self._connection.execute("""
                INSERT OR REPLACE INTO trace_spans(
                    span_id, trace_id, run_id, attempt_id, parent_span_id, name, kind, status,
                    started_at, ended_at, duration_ms, input_tokens, output_tokens, total_tokens,
                    cost, error_type, attributes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                span["span_id"], span["trace_id"], span["run_id"], span["attempt_id"],
                span.get("parent_span_id"), span["name"], span["kind"], span["status"],
                span["started_at"], span["ended_at"], span["duration_ms"],
                span.get("input_tokens", 0), span.get("output_tokens", 0), span.get("total_tokens", 0),
                span.get("cost", 0.0), span.get("error_type"),
                json.dumps(span.get("attributes", {}), ensure_ascii=False, default=str),
            ))

    def finish_trace(self, trace_id: str, *, status: str, ended_at: str, duration_ms: float,
                     replan_count: int = 0) -> None:
        with self._lock, self._connection:
            metrics = self._connection.execute("""
                SELECT COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(cost), 0) AS cost,
                       SUM(CASE WHEN kind='tool' THEN 1 ELSE 0 END) AS tool_calls,
                       SUM(CASE WHEN kind='tool' AND status='OK' THEN 1 ELSE 0 END) AS tool_successes,
                       SUM(CASE WHEN status='ERROR' THEN 1 ELSE 0 END) AS error_count
                FROM trace_spans WHERE trace_id=?
            """, (trace_id,)).fetchone()
            self._connection.execute("""
                UPDATE trace_runs SET status=?, ended_at=?, duration_ms=?, input_tokens=?, output_tokens=?,
                    total_tokens=?, cost=?, tool_calls=?, tool_successes=?, error_count=?, replan_count=?
                WHERE trace_id=?
            """, (
                status, ended_at, duration_ms, metrics["input_tokens"], metrics["output_tokens"],
                metrics["total_tokens"], round(float(metrics["cost"] or 0), 8), metrics["tool_calls"],
                metrics["tool_successes"], metrics["error_count"], replan_count, trace_id,
            ))

    @staticmethod
    def _decode_run(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
        return result

    @staticmethod
    def _decode_span(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["attributes"] = json.loads(result.pop("attributes_json") or "{}")
        return result

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT latest.*,
                       aggregate.attempt_count,
                       aggregate.aggregate_duration_ms,
                       aggregate.aggregate_input_tokens,
                       aggregate.aggregate_output_tokens,
                       aggregate.aggregate_total_tokens,
                       aggregate.aggregate_cost,
                       aggregate.aggregate_tool_calls,
                       aggregate.aggregate_tool_successes,
                       aggregate.aggregate_error_count,
                       aggregate.aggregate_replan_count
                FROM trace_runs AS latest
                JOIN (
                    SELECT run_id, MAX(attempt_id) AS latest_attempt, COUNT(*) AS attempt_count,
                           SUM(COALESCE(duration_ms, 0)) AS aggregate_duration_ms,
                           SUM(input_tokens) AS aggregate_input_tokens,
                           SUM(output_tokens) AS aggregate_output_tokens,
                           SUM(total_tokens) AS aggregate_total_tokens,
                           SUM(cost) AS aggregate_cost,
                           SUM(tool_calls) AS aggregate_tool_calls,
                           SUM(tool_successes) AS aggregate_tool_successes,
                           SUM(error_count) AS aggregate_error_count,
                           MAX(replan_count) AS aggregate_replan_count
                    FROM trace_runs GROUP BY run_id
                ) AS aggregate
                  ON latest.run_id=aggregate.run_id AND latest.attempt_id=aggregate.latest_attempt
                ORDER BY latest.started_at DESC LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
        result = []
        for row in rows:
            item = self._decode_run(row)
            for name in ("duration_ms", "input_tokens", "output_tokens", "total_tokens", "cost",
                         "tool_calls", "tool_successes", "error_count", "replan_count"):
                item[name] = item.pop(f"aggregate_{name}")
            item["cost"] = round(float(item["cost"] or 0), 8)
            result.append(item)
        return result

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            attempts = self._connection.execute(
                "SELECT * FROM trace_runs WHERE run_id=? ORDER BY attempt_id", (run_id,)
            ).fetchall()
            spans = self._connection.execute(
                "SELECT * FROM trace_spans WHERE run_id=? ORDER BY started_at, span_id", (run_id,)
            ).fetchall()
        if not attempts:
            return None
        attempt_rows = [self._decode_run(row) for row in attempts]
        span_rows = [self._decode_span(row) for row in spans]
        summary = {
            "duration_ms": round(sum(float(row.get("duration_ms") or 0) for row in attempt_rows), 2),
            "input_tokens": sum(row["input_tokens"] for row in attempt_rows),
            "output_tokens": sum(row["output_tokens"] for row in attempt_rows),
            "total_tokens": sum(row["total_tokens"] for row in attempt_rows),
            "cost": round(sum(float(row["cost"]) for row in attempt_rows), 8),
            "cost_currency": attempt_rows[-1]["cost_currency"],
            "tool_calls": sum(row["tool_calls"] for row in attempt_rows),
            "tool_successes": sum(row["tool_successes"] for row in attempt_rows),
            "error_count": sum(row["error_count"] for row in attempt_rows),
            "replan_count": max(row["replan_count"] for row in attempt_rows),
            "attempt_count": len(attempt_rows),
        }
        return {"run_id": run_id, "status": attempt_rows[-1]["status"], "summary": summary,
                "metadata": attempt_rows[-1]["metadata"], "attempts": attempt_rows, "spans": span_rows}

    def summary(self, limit: int = 200) -> dict[str, Any]:
        runs = self.list_runs(limit)
        durations = sorted(float(row.get("duration_ms") or 0) for row in runs if row.get("duration_ms") is not None)
        p95 = durations[min(len(durations) - 1, int((len(durations) - 1) * 0.95))] if durations else None
        status_counts: dict[str, int] = {}
        for row in runs:
            status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
        tool_calls = sum(row["tool_calls"] for row in runs)
        return {
            "run_count": len(runs),
            "success_rate": round(sum(row["status"] == "COMPLETED" for row in runs) / len(runs), 4) if runs else None,
            "error_rate": round(sum(row["status"] == "FAILED" for row in runs) / len(runs), 4) if runs else None,
            "timeout_rate": round(sum(row["status"] == "TIMED_OUT" for row in runs) / len(runs), 4) if runs else None,
            "p95_duration_ms": p95,
            "average_duration_ms": round(sum(durations) / len(durations), 3) if durations else None,
            "total_tokens": sum(row["total_tokens"] for row in runs),
            "total_cost": round(sum(float(row["cost"]) for row in runs), 8),
            "average_cost": round(sum(float(row["cost"]) for row in runs) / len(runs), 8) if runs else None,
            "average_replans": round(sum(row["replan_count"] for row in runs) / len(runs), 4) if runs else None,
            "tool_success_rate": round(
                sum(row["tool_successes"] for row in runs) / tool_calls, 4
            ) if tool_calls else None,
            "status_counts": status_counts,
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()
