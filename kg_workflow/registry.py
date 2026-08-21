"""SQLite control plane for KG build runs, steps, releases and watermarks."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KGWorkflowRegistry:
    def __init__(self, path: str | Path = ".runtime/kg-workflow.sqlite"):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(target)
        self.connection.row_factory = sqlite3.Row
        with self.connection:
            self.connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS kg_runs (
                    run_id TEXT PRIMARY KEY,
                    release_id TEXT NOT NULL UNIQUE,
                    run_type TEXT NOT NULL,
                    source_database TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS kg_steps (
                    run_id TEXT NOT NULL,
                    step_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    output_json TEXT,
                    error_json TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    PRIMARY KEY (run_id, step_name),
                    FOREIGN KEY (run_id) REFERENCES kg_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS kg_releases (
                    release_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    artifact_uri TEXT NOT NULL,
                    neo4j_database TEXT,
                    milvus_collection TEXT,
                    quality_json TEXT NOT NULL,
                    activated_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS kg_active_release (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    release_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS kg_watermarks (
                    source_database TEXT NOT NULL,
                    dataset TEXT NOT NULL,
                    watermark_json TEXT NOT NULL,
                    release_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source_database, dataset)
                );
                CREATE TABLE IF NOT EXISTS kg_inbox_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    dataset TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    processed_at TEXT NOT NULL
                );
            """)

    def create_run(self, run_id: str, release_id: str, run_type: str,
                   source_database: str, config: dict[str, Any]) -> None:
        now = _now()
        with self.connection:
            self.connection.execute("""
                INSERT INTO kg_runs(run_id,release_id,run_type,source_database,status,config_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
            """, (run_id, release_id, run_type, source_database, "PLANNED",
                  json.dumps(config, ensure_ascii=False, sort_keys=True), now, now))

    def run(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM kg_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["config"] = json.loads(result.pop("config_json"))
        result["error"] = json.loads(result.pop("error_json") or "null")
        return result

    def set_run_status(self, run_id: str, status: str, error: dict[str, Any] | None = None) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE kg_runs SET status=?,error_json=?,updated_at=? WHERE run_id=?",
                (status, json.dumps(error, ensure_ascii=False) if error else None, _now(), run_id),
            )

    def step(self, run_id: str, step_name: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM kg_steps WHERE run_id=? AND step_name=?", (run_id, step_name)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["output"] = json.loads(result.pop("output_json") or "{}")
        result["error"] = json.loads(result.pop("error_json") or "null")
        return result

    def start_step(self, run_id: str, step_name: str) -> int:
        existing = self.step(run_id, step_name)
        attempt = int(existing["attempt"] if existing else 0) + 1
        with self.connection:
            self.connection.execute("""
                INSERT INTO kg_steps(run_id,step_name,status,attempt,started_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(run_id,step_name) DO UPDATE SET
                    status='RUNNING',attempt=excluded.attempt,output_json=NULL,error_json=NULL,
                    started_at=excluded.started_at,completed_at=NULL
            """, (run_id, step_name, "RUNNING", attempt, _now()))
        return attempt

    def complete_step(self, run_id: str, step_name: str, output: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute("""
                UPDATE kg_steps SET status='COMPLETED',output_json=?,error_json=NULL,completed_at=?
                WHERE run_id=? AND step_name=?
            """, (json.dumps(output, ensure_ascii=False, sort_keys=True), _now(), run_id, step_name))

    def fail_step(self, run_id: str, step_name: str, error: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute("""
                UPDATE kg_steps SET status='FAILED',error_json=?,completed_at=?
                WHERE run_id=? AND step_name=?
            """, (json.dumps(error, ensure_ascii=False), _now(), run_id, step_name))

    def register_release(self, release_id: str, run_id: str, artifact_uri: str,
                         neo4j_database: str, milvus_collection: str,
                         quality: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute("""
                INSERT INTO kg_releases(release_id,run_id,status,artifact_uri,neo4j_database,
                    milvus_collection,quality_json,created_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(release_id) DO UPDATE SET status='VERIFIED',artifact_uri=excluded.artifact_uri,
                    neo4j_database=excluded.neo4j_database,milvus_collection=excluded.milvus_collection,
                    quality_json=excluded.quality_json
            """, (release_id, run_id, "VERIFIED", artifact_uri, neo4j_database,
                  milvus_collection, json.dumps(quality, ensure_ascii=False, sort_keys=True), _now()))

    def activate(self, release_id: str, source_database: str,
                 watermarks: dict[str, Any]) -> None:
        now = _now()
        with self.connection:
            self.connection.execute("UPDATE kg_releases SET status='INACTIVE' WHERE status='ACTIVE'")
            self.connection.execute(
                "UPDATE kg_releases SET status='ACTIVE',activated_at=? WHERE release_id=?", (now, release_id)
            )
            self.connection.execute("""
                INSERT INTO kg_active_release(singleton,release_id,updated_at) VALUES(1,?,?)
                ON CONFLICT(singleton) DO UPDATE SET release_id=excluded.release_id,updated_at=excluded.updated_at
            """, (release_id, now))
            for dataset, watermark in watermarks.items():
                self.connection.execute("""
                    INSERT INTO kg_watermarks(source_database,dataset,watermark_json,release_id,updated_at)
                    VALUES(?,?,?,?,?) ON CONFLICT(source_database,dataset) DO UPDATE SET
                        watermark_json=excluded.watermark_json,release_id=excluded.release_id,
                        updated_at=excluded.updated_at
                """, (source_database, dataset, json.dumps(watermark, ensure_ascii=False), release_id, now))

    def active_release(self) -> dict[str, Any] | None:
        row = self.connection.execute("""
            SELECT r.* FROM kg_active_release a JOIN kg_releases r ON r.release_id=a.release_id
            WHERE a.singleton=1
        """).fetchone()
        if not row:
            return None
        result = dict(row)
        result["quality"] = json.loads(result.pop("quality_json"))
        return result

    def watermarks(self, source_database: str) -> dict[str, Any]:
        rows = self.connection.execute(
            "SELECT dataset,watermark_json FROM kg_watermarks WHERE source_database=?",
            (source_database,),
        ).fetchall()
        return {row["dataset"]: json.loads(row["watermark_json"]) for row in rows}

    def record_events(self, run_id: str, events: list[dict[str, Any]]) -> tuple[int, int]:
        inserted = duplicate = 0
        with self.connection:
            for event in events:
                cursor = self.connection.execute("""
                    INSERT OR IGNORE INTO kg_inbox_events(event_id,run_id,dataset,record_id,
                        operation,payload_hash,status,processed_at)
                    VALUES(?,?,?,?,?,?,?,?)
                """, (event["event_id"], run_id, event["dataset"], event["record_id"],
                      event["operation"], event["payload_hash"], "PROCESSED", _now()))
                if cursor.rowcount:
                    inserted += 1
                else:
                    duplicate += 1
        return inserted, duplicate

    def close(self) -> None:
        self.connection.close()
