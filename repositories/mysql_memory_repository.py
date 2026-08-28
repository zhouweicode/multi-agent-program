"""MySQL production storage for all application memory layers."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from models.settings import Settings
from services.memory_errors import MemoryRevisionConflict

_DATABASE_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _normalized_hash(content: str) -> str:
    normalized = " ".join(content.split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key, value in list(result.items()):
        if isinstance(value, datetime):
            result[key] = value.replace(tzinfo=UTC).isoformat()
    return result


class MySQLMemoryRepository:
    backend = "mysql"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.database = self.settings.memory_mysql_database
        if not self.settings.mysql_password:
            raise ValueError("MYSQL_PASSWORD 未配置")
        if not _DATABASE_RE.fullmatch(self.database):
            raise ValueError("MEMORY_MYSQL_DATABASE 只能包含字母、数字和下划线")
        self._initialize_schema()

    def _connect(self, database: str | None = None):
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError("请安装 PyMySQL") from exc
        return pymysql.connect(
            host=self.settings.mysql_host,
            port=self.settings.mysql_port,
            user=self.settings.mysql_user,
            password=self.settings.mysql_password,
            database=database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
            read_timeout=15,
            write_timeout=15,
            autocommit=False,
        )

    @contextmanager
    def _cursor(self) -> Iterator[tuple[Any, Any]]:
        connection = self._connect(self.database)
        try:
            with connection.cursor() as cursor:
                yield connection, cursor
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.database}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            connection.commit()
        finally:
            connection.close()

        statements = (
            """CREATE TABLE IF NOT EXISTS memory_schema_meta (
                schema_key VARCHAR(64) PRIMARY KEY,
                schema_value VARCHAR(255) NOT NULL,
                updated_at DATETIME(6) NOT NULL
            ) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS memory_conversations (
                user_id VARCHAR(64) NOT NULL,
                conversation_id VARCHAR(128) NOT NULL,
                created_at DATETIME(6) NOT NULL,
                updated_at DATETIME(6) NOT NULL,
                PRIMARY KEY(user_id, conversation_id),
                INDEX idx_memory_conversations_updated(user_id, updated_at)
            ) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS memory_turns (
                turn_id BIGINT PRIMARY KEY AUTO_INCREMENT,
                user_id VARCHAR(64) NOT NULL,
                conversation_id VARCHAR(128) NOT NULL,
                run_id VARCHAR(128) NOT NULL UNIQUE,
                original_question TEXT NOT NULL,
                contextualized_question TEXT NOT NULL,
                final_answer LONGTEXT,
                intent VARCHAR(128),
                primary_domain VARCHAR(64),
                resolved_entities_json JSON NOT NULL,
                created_at DATETIME(6) NOT NULL,
                INDEX idx_memory_turns_conversation(user_id, conversation_id, turn_id),
                CONSTRAINT fk_memory_turns_conversation
                    FOREIGN KEY(user_id, conversation_id)
                    REFERENCES memory_conversations(user_id, conversation_id)
                    ON DELETE CASCADE
            ) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS memory_entities (
                user_id VARCHAR(64) NOT NULL,
                conversation_id VARCHAR(128) NOT NULL,
                entity_id VARCHAR(255) NOT NULL,
                name VARCHAR(255) NOT NULL,
                organization VARCHAR(512),
                title VARCHAR(255),
                entity_type VARCHAR(64) NOT NULL DEFAULT 'scholar',
                mention_count INT NOT NULL DEFAULT 1,
                last_seen_turn BIGINT NOT NULL,
                updated_at DATETIME(6) NOT NULL,
                PRIMARY KEY(user_id, conversation_id, entity_id),
                INDEX idx_memory_entities_focus(
                    user_id, conversation_id, last_seen_turn, mention_count
                ),
                CONSTRAINT fk_memory_entities_conversation
                    FOREIGN KEY(user_id, conversation_id)
                    REFERENCES memory_conversations(user_id, conversation_id)
                    ON DELETE CASCADE
            ) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS memory_profiles (
                user_id VARCHAR(64) NOT NULL,
                agent_name VARCHAR(64) NOT NULL DEFAULT '',
                profile_json JSON NOT NULL,
                revision INT NOT NULL DEFAULT 1,
                created_at DATETIME(6) NOT NULL,
                updated_at DATETIME(6) NOT NULL,
                PRIMARY KEY(user_id, agent_name)
            ) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS memory_facts (
                fact_id VARCHAR(64) PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                agent_name VARCHAR(64) NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                normalized_hash CHAR(64) NOT NULL,
                category VARCHAR(64) NOT NULL,
                confidence DECIMAL(6,5) NOT NULL,
                source_run_id VARCHAR(128),
                source_conversation_id VARCHAR(128),
                expected_valid_until DATETIME(6),
                status VARCHAR(32) NOT NULL DEFAULT 'active',
                revision INT NOT NULL DEFAULT 1,
                recall_count INT NOT NULL DEFAULT 0,
                application_count INT NOT NULL DEFAULT 0,
                last_recalled_at DATETIME(6),
                last_applied_at DATETIME(6),
                created_at DATETIME(6) NOT NULL,
                updated_at DATETIME(6) NOT NULL,
                UNIQUE KEY uq_memory_fact_content(user_id, agent_name, normalized_hash),
                INDEX idx_memory_facts_scope(user_id, agent_name, status, category, updated_at)
            ) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS memory_update_jobs (
                job_id VARCHAR(64) PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                agent_name VARCHAR(64) NOT NULL DEFAULT '',
                conversation_id VARCHAR(128),
                run_id VARCHAR(128) NOT NULL,
                payload_json JSON NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                attempt_count INT NOT NULL DEFAULT 0,
                available_at DATETIME(6) NOT NULL,
                error TEXT,
                created_at DATETIME(6) NOT NULL,
                updated_at DATETIME(6) NOT NULL,
                UNIQUE KEY uq_memory_job_run(user_id, agent_name, run_id),
                INDEX idx_memory_jobs_pending(status, available_at)
            ) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS memory_audit_logs (
                audit_id BIGINT PRIMARY KEY AUTO_INCREMENT,
                user_id VARCHAR(64) NOT NULL,
                agent_name VARCHAR(64) NOT NULL DEFAULT '',
                operation VARCHAR(64) NOT NULL,
                target_type VARCHAR(64) NOT NULL,
                target_id VARCHAR(128),
                metadata_json JSON,
                created_at DATETIME(6) NOT NULL,
                INDEX idx_memory_audit_user(user_id, created_at)
            ) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS query_experience_patterns (
                pattern_id VARCHAR(64) PRIMARY KEY,
                scope_type VARCHAR(16) NOT NULL,
                scope_id VARCHAR(128) NOT NULL,
                query_template TEXT NOT NULL,
                strategy_json JSON NOT NULL,
                sample_count INT NOT NULL DEFAULT 0,
                success_count INT NOT NULL DEFAULT 0,
                failure_count INT NOT NULL DEFAULT 0,
                average_quality DOUBLE NOT NULL DEFAULT 0,
                average_duration_ms DOUBLE NOT NULL DEFAULT 0,
                average_tokens DOUBLE NOT NULL DEFAULT 0,
                average_cost DOUBLE NOT NULL DEFAULT 0,
                created_at DATETIME(6) NOT NULL,
                updated_at DATETIME(6) NOT NULL,
                INDEX idx_experience_patterns_scope(
                    scope_type, scope_id, success_count, updated_at
                )
            ) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS query_experience_events (
                event_id VARCHAR(64) PRIMARY KEY,
                run_id VARCHAR(128) NOT NULL,
                pattern_id VARCHAR(64) NOT NULL,
                scope_type VARCHAR(16) NOT NULL,
                scope_id VARCHAR(128) NOT NULL,
                normalized_question TEXT NOT NULL,
                query_template TEXT NOT NULL,
                strategy_json JSON NOT NULL,
                outcome VARCHAR(32) NOT NULL,
                eligible BOOLEAN NOT NULL,
                validation_pass BOOLEAN NOT NULL,
                quality_score DOUBLE NOT NULL,
                duration_ms DOUBLE,
                total_tokens BIGINT,
                cost DOUBLE,
                created_at DATETIME(6) NOT NULL,
                UNIQUE KEY uq_experience_event_run(scope_type, scope_id, run_id),
                INDEX idx_experience_events_pattern(pattern_id, created_at),
                INDEX idx_experience_events_run(run_id, scope_type, scope_id),
                CONSTRAINT fk_experience_event_pattern FOREIGN KEY(pattern_id)
                    REFERENCES query_experience_patterns(pattern_id)
            ) ENGINE=InnoDB""",
        )
        with self._cursor() as (connection, cursor):
            for statement in statements:
                cursor.execute(statement)
            cursor.execute("""
                SELECT COLUMN_NAME FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA=%s AND TABLE_NAME='memory_facts'
            """, (self.database,))
            columns = {row["COLUMN_NAME"] for row in cursor.fetchall()}
            for name, definition in (
                ("recall_count", "INT NOT NULL DEFAULT 0"),
                ("application_count", "INT NOT NULL DEFAULT 0"),
                ("last_recalled_at", "DATETIME(6) NULL"),
                ("last_applied_at", "DATETIME(6) NULL"),
            ):
                if name not in columns:
                    cursor.execute(
                        f"ALTER TABLE memory_facts ADD COLUMN {name} {definition}"
                    )
            cursor.execute(f"""
                UPDATE memory_facts
                SET expected_valid_until=DATE_ADD(
                    updated_at, INTERVAL {self.settings.memory_fact_review_days} DAY
                )
                WHERE expected_valid_until IS NULL
            """)
            cursor.execute("""
                INSERT INTO memory_schema_meta(schema_key, schema_value, updated_at)
                VALUES ('schema_version', '2', %s)
                ON DUPLICATE KEY UPDATE schema_value=VALUES(schema_value),
                                        updated_at=VALUES(updated_at)
            """, (_now(),))
            connection.commit()

    def health(self) -> dict[str, Any]:
        with self._cursor() as (_, cursor):
            cursor.execute("SELECT schema_value FROM memory_schema_meta WHERE schema_key='schema_version'")
            row = cursor.fetchone()
        return {"backend": "mysql", "database": self.database,
                "schema_version": row["schema_value"] if row else None,
                "ready": bool(row)}

    # Conversation memory -------------------------------------------------
    def ensure_conversation(self, user_id: str, conversation_id: str) -> None:
        now = _now()
        with self._cursor() as (connection, cursor):
            cursor.execute("""
                INSERT INTO memory_conversations(user_id, conversation_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE updated_at=VALUES(updated_at)
            """, (user_id, conversation_id, now, now))
            connection.commit()

    def exists(self, user_id: str, conversation_id: str) -> bool:
        with self._cursor() as (_, cursor):
            cursor.execute("""SELECT 1 FROM memory_conversations
                              WHERE user_id=%s AND conversation_id=%s""",
                           (user_id, conversation_id))
            return cursor.fetchone() is not None

    def record_turn(self, user_id: str, conversation_id: str, run_id: str,
                    original_question: str, contextualized_question: str,
                    final_answer: str | None, intent: str | None,
                    primary_domain: str | None,
                    entities: list[dict[str, Any]]) -> dict[str, Any]:
        now = _now()
        with self._cursor() as (connection, cursor):
            try:
                cursor.execute("""
                    INSERT INTO memory_conversations(user_id, conversation_id, created_at, updated_at)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE updated_at=VALUES(updated_at)
                """, (user_id, conversation_id, now, now))
                cursor.execute("""
                    INSERT IGNORE INTO memory_turns(
                        user_id, conversation_id, run_id, original_question,
                        contextualized_question, final_answer, intent, primary_domain,
                        resolved_entities_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (user_id, conversation_id, run_id, original_question,
                      contextualized_question, final_answer, intent, primary_domain,
                      json.dumps({row["name"]: row["entity_id"] for row in entities},
                                 ensure_ascii=False), now))
                if cursor.rowcount:
                    turn_id = int(cursor.lastrowid)
                    for entity in entities:
                        cursor.execute("""
                            INSERT INTO memory_entities(
                                user_id, conversation_id, entity_id, name, organization,
                                title, entity_type, mention_count, last_seen_turn, updated_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
                            ON DUPLICATE KEY UPDATE
                                name=VALUES(name),
                                organization=COALESCE(VALUES(organization), organization),
                                title=COALESCE(VALUES(title), title),
                                entity_type=VALUES(entity_type),
                                mention_count=mention_count+1,
                                last_seen_turn=VALUES(last_seen_turn),
                                updated_at=VALUES(updated_at)
                        """, (user_id, conversation_id, entity["entity_id"], entity["name"],
                              entity.get("organization"), entity.get("title"),
                              entity.get("entity_type", "scholar"), turn_id, now))
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get(user_id, conversation_id)

    def get(self, user_id: str, conversation_id: str,
            turn_limit: int = 10) -> dict[str, Any]:
        with self._cursor() as (_, cursor):
            cursor.execute("""SELECT * FROM memory_conversations
                              WHERE user_id=%s AND conversation_id=%s""",
                           (user_id, conversation_id))
            conversation = cursor.fetchone()
            if not conversation:
                return {"user_id": user_id, "conversation_id": conversation_id,
                        "turn_count": 0, "entities": [], "turns": []}
            cursor.execute("""
                SELECT entity_id, name, organization, title, entity_type,
                       mention_count, last_seen_turn, updated_at
                FROM memory_entities WHERE user_id=%s AND conversation_id=%s
                ORDER BY last_seen_turn DESC, mention_count DESC, entity_id
            """, (user_id, conversation_id))
            entities = [_public_row(row) for row in cursor.fetchall()]
            cursor.execute("""
                SELECT run_id, original_question, contextualized_question, intent,
                       primary_domain, resolved_entities_json, created_at
                FROM memory_turns WHERE user_id=%s AND conversation_id=%s
                ORDER BY turn_id DESC LIMIT %s
            """, (user_id, conversation_id, max(1, min(turn_limit, 50))))
            turns = []
            for row in cursor.fetchall():
                item = _public_row(row)
                raw = item.pop("resolved_entities_json") or {}
                item["resolved_entities"] = json.loads(raw) if isinstance(raw, str) else raw
                turns.append(item)
            cursor.execute("""SELECT COUNT(*) AS count FROM memory_turns
                              WHERE user_id=%s AND conversation_id=%s""",
                           (user_id, conversation_id))
            count = int(cursor.fetchone()["count"])
        conversation = _public_row(conversation)
        return {"user_id": user_id, "conversation_id": conversation_id,
                "created_at": conversation["created_at"],
                "updated_at": conversation["updated_at"], "turn_count": count,
                "entities": entities, "turns": turns}

    def clear(self, user_id: str, conversation_id: str) -> dict[str, Any]:
        with self._cursor() as (connection, cursor):
            cursor.execute("""SELECT COUNT(*) AS count FROM memory_turns
                              WHERE user_id=%s AND conversation_id=%s""",
                           (user_id, conversation_id))
            turns = int(cursor.fetchone()["count"])
            cursor.execute("""SELECT COUNT(*) AS count FROM memory_entities
                              WHERE user_id=%s AND conversation_id=%s""",
                           (user_id, conversation_id))
            entities = int(cursor.fetchone()["count"])
            cursor.execute("""DELETE FROM memory_conversations
                              WHERE user_id=%s AND conversation_id=%s""",
                           (user_id, conversation_id))
            connection.commit()
        return {"user_id": user_id, "conversation_id": conversation_id,
                "cleared": True, "deleted_turns": turns,
                "deleted_entities": entities}

    def clear_user_conversations(self, user_id: str) -> dict[str, int]:
        with self._cursor() as (connection, cursor):
            cursor.execute("SELECT COUNT(*) count FROM memory_conversations WHERE user_id=%s",
                           (user_id,))
            conversations = int(cursor.fetchone()["count"])
            cursor.execute("SELECT COUNT(*) count FROM memory_turns WHERE user_id=%s",
                           (user_id,))
            turns = int(cursor.fetchone()["count"])
            cursor.execute("SELECT COUNT(*) count FROM memory_entities WHERE user_id=%s",
                           (user_id,))
            entities = int(cursor.fetchone()["count"])
            cursor.execute("DELETE FROM memory_conversations WHERE user_id=%s", (user_id,))
            connection.commit()
        return {"deleted_conversations": conversations, "deleted_turns": turns,
                "deleted_entities": entities}

    # Long-term facts and durable extraction queue ------------------------
    def search(self, user_id: str, query: str, top_k: int = 5,
               agent_name: str | None = None) -> list[dict[str, Any]]:
        pattern = f"%{query.strip()}%"
        with self._cursor() as (_, cursor):
            cursor.execute("""
                SELECT * FROM memory_facts
                WHERE user_id=%s AND agent_name=%s AND status='active'
                  AND (%s='' OR content LIKE %s)
                ORDER BY confidence DESC, updated_at DESC, fact_id LIMIT %s
            """, (user_id, agent_name or "", query.strip(), pattern,
                  max(1, min(top_k, 100))))
            return [_public_row(row) for row in cursor.fetchall()]

    def list_facts(self, user_id: str, limit: int = 100,
                   agent_name: str | None = None,
                   include_archived: bool = False) -> list[dict[str, Any]]:
        status_clause = "" if include_archived else "AND status='active'"
        with self._cursor() as (_, cursor):
            cursor.execute(f"""
                SELECT * FROM memory_facts
                WHERE user_id=%s AND agent_name=%s {status_clause}
                ORDER BY updated_at DESC, fact_id LIMIT %s
            """, (user_id, agent_name or "", max(1, min(limit, 1000))))
            return [_public_row(row) for row in cursor.fetchall()]

    def create(self, user_id: str, content: str, category: str = "context",
               confidence: float = 0.8, agent_name: str | None = None,
               source_run_id: str | None = None,
               source_conversation_id: str | None = None,
               expected_valid_until: str | None = None) -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError("记忆事实不能为空")
        now, digest = _now(), _normalized_hash(content)
        with self._cursor() as (connection, cursor):
            cursor.execute("""
                INSERT INTO memory_facts(
                    fact_id, user_id, agent_name, content, normalized_hash,
                    category, confidence, source_run_id, source_conversation_id,
                    expected_valid_until, status, revision, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          'active', 1, %s, %s)
                ON DUPLICATE KEY UPDATE
                    confidence=GREATEST(confidence, VALUES(confidence)),
                    updated_at=VALUES(updated_at)
            """, (f"fact-{uuid4().hex}", user_id, agent_name or "", content, digest,
                  category, max(0.0, min(float(confidence), 1.0)), source_run_id,
                  source_conversation_id, expected_valid_until, now, now))
            connection.commit()
            cursor.execute("""SELECT * FROM memory_facts
                              WHERE user_id=%s AND agent_name=%s AND normalized_hash=%s""",
                           (user_id, agent_name or "", digest))
            return _public_row(cursor.fetchone())

    def get_fact(self, user_id: str, fact_id: str,
                 agent_name: str | None = None) -> dict[str, Any]:
        with self._cursor() as (_, cursor):
            cursor.execute("""SELECT * FROM memory_facts
                              WHERE user_id=%s AND agent_name=%s AND fact_id=%s""",
                           (user_id, agent_name or "", fact_id))
            row = cursor.fetchone()
        if not row:
            raise KeyError(fact_id)
        return _public_row(row)

    def update(self, user_id: str, fact_id: str, changes: dict[str, Any],
               agent_name: str | None = None,
               expected_revision: int | None = None) -> dict[str, Any]:
        allowed = {
            "content", "category", "confidence", "expected_valid_until", "status",
            "source_run_id", "source_conversation_id",
        }
        updates = {key: value for key, value in changes.items() if key in allowed}
        if not updates:
            return self.get_fact(user_id, fact_id, agent_name)
        if "content" in updates:
            updates["content"] = str(updates["content"]).strip()
            if not updates["content"]:
                raise ValueError("记忆事实不能为空")
            updates["normalized_hash"] = _normalized_hash(updates["content"])
        if "confidence" in updates:
            updates["confidence"] = max(0.0, min(float(updates["confidence"]), 1.0))
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{key}=%s" for key in updates)
        with self._cursor() as (connection, cursor):
            revision_clause = " AND revision=%s" if expected_revision is not None else ""
            cursor.execute(
                f"""UPDATE memory_facts SET {assignments}, revision=revision+1
                    WHERE user_id=%s AND agent_name=%s AND fact_id=%s{revision_clause}""",
                (*updates.values(), user_id, agent_name or "", fact_id,
                 *((expected_revision,) if expected_revision is not None else ())),
            )
            if not cursor.rowcount:
                cursor.execute("""SELECT revision FROM memory_facts
                                  WHERE user_id=%s AND agent_name=%s AND fact_id=%s""",
                               (user_id, agent_name or "", fact_id))
                row = cursor.fetchone()
                connection.rollback()
                if not row:
                    raise KeyError(fact_id)
                raise MemoryRevisionConflict(
                    fact_id, int(expected_revision), int(row["revision"])
                )
            connection.commit()
        return self.get_fact(user_id, fact_id, agent_name)

    def mark_recalled(self, user_id: str, fact_ids: list[str],
                      agent_name: str | None = None) -> int:
        if not fact_ids:
            return 0
        placeholders = ",".join(["%s"] * len(fact_ids))
        with self._cursor() as (connection, cursor):
            cursor.execute(f"""
                UPDATE memory_facts SET recall_count=recall_count+1,
                    last_recalled_at=%s
                WHERE user_id=%s AND agent_name=%s AND fact_id IN ({placeholders})
            """, (_now(), user_id, agent_name or "", *fact_ids))
            connection.commit()
            return int(cursor.rowcount)

    def mark_applied(self, user_id: str, fact_ids: list[str],
                     agent_name: str | None = None) -> int:
        if not fact_ids:
            return 0
        placeholders = ",".join(["%s"] * len(fact_ids))
        with self._cursor() as (connection, cursor):
            cursor.execute(f"""
                UPDATE memory_facts SET application_count=application_count+1,
                    last_applied_at=%s
                WHERE user_id=%s AND agent_name=%s AND fact_id IN ({placeholders})
            """, (_now(), user_id, agent_name or "", *fact_ids))
            connection.commit()
            return int(cursor.rowcount)

    def audit(self, user_id: str, operation: str, target_type: str,
              target_id: str | None = None, metadata: dict[str, Any] | None = None,
              agent_name: str | None = None) -> int:
        with self._cursor() as (connection, cursor):
            cursor.execute("""
                INSERT INTO memory_audit_logs(
                    user_id, agent_name, operation, target_type, target_id,
                    metadata_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (user_id, agent_name or "", operation, target_type, target_id,
                  json.dumps(metadata or {}, ensure_ascii=False), _now()))
            connection.commit()
            return int(cursor.lastrowid)

    def list_audit_logs(self, user_id: str, limit: int = 100,
                        agent_name: str | None = None) -> list[dict[str, Any]]:
        with self._cursor() as (_, cursor):
            cursor.execute("""
                SELECT * FROM memory_audit_logs
                WHERE user_id=%s AND agent_name=%s
                ORDER BY audit_id DESC LIMIT %s
            """, (user_id, agent_name or "", max(1, min(limit, 500))))
            rows = cursor.fetchall()
        result = []
        for row in rows:
            item = _public_row(row)
            raw = item.pop("metadata_json") or {}
            item["metadata"] = json.loads(raw) if isinstance(raw, str) else raw
            result.append(item)
        return result

    def delete(self, user_id: str, fact_id: str,
               agent_name: str | None = None,
               expected_revision: int | None = None) -> bool:
        with self._cursor() as (connection, cursor):
            revision_clause = " AND revision=%s" if expected_revision is not None else ""
            cursor.execute("""DELETE FROM memory_facts
                              WHERE user_id=%s AND agent_name=%s AND fact_id=%s"""
                           + revision_clause,
                           (user_id, agent_name or "", fact_id,
                            *((expected_revision,)
                              if expected_revision is not None else ())))
            if not cursor.rowcount and expected_revision is not None:
                cursor.execute("""SELECT revision FROM memory_facts
                                  WHERE user_id=%s AND agent_name=%s AND fact_id=%s""",
                               (user_id, agent_name or "", fact_id))
                row = cursor.fetchone()
                if row:
                    connection.rollback()
                    raise MemoryRevisionConflict(
                        fact_id, expected_revision, int(row["revision"])
                    )
            connection.commit()
            return bool(cursor.rowcount)

    def clear_facts(self, user_id: str, agent_name: str | None = None) -> int:
        with self._cursor() as (connection, cursor):
            cursor.execute("DELETE FROM memory_facts WHERE user_id=%s AND agent_name=%s",
                           (user_id, agent_name or ""))
            connection.commit()
            return int(cursor.rowcount)

    def enqueue(self, user_id: str, run_id: str, payload: dict[str, Any],
                conversation_id: str | None = None,
                agent_name: str | None = None) -> bool:
        now = _now()
        with self._cursor() as (connection, cursor):
            cursor.execute("""
                INSERT IGNORE INTO memory_update_jobs(
                    job_id, user_id, agent_name, conversation_id, run_id,
                    payload_json, status, available_at, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s)
            """, (f"memjob-{uuid4().hex}", user_id, agent_name or "",
                  conversation_id, run_id, json.dumps(payload, ensure_ascii=False),
                  now, now, now))
            connection.commit()
            return bool(cursor.rowcount)

    @staticmethod
    def _job(row: dict[str, Any]) -> dict[str, Any]:
        job = _public_row(row)
        raw = job.pop("payload_json")
        job["payload"] = json.loads(raw) if isinstance(raw, str) else raw
        return job

    def claim_jobs(self, limit: int = 10, lease_seconds: int = 60) -> list[dict[str, Any]]:
        """Lease jobs in one transaction; expired processing leases are reclaimable."""
        now = _now()
        lease_until = now + timedelta(seconds=max(5, lease_seconds))
        with self._cursor() as (connection, cursor):
            try:
                cursor.execute("""
                    SELECT * FROM memory_update_jobs
                    WHERE available_at <= %s
                      AND status IN ('pending', 'retry', 'processing')
                    ORDER BY available_at, created_at, job_id
                    LIMIT %s FOR UPDATE
                """, (now, max(1, min(limit, 100))))
                rows = list(cursor.fetchall())
                if rows:
                    placeholders = ",".join(["%s"] * len(rows))
                    cursor.execute(f"""
                        UPDATE memory_update_jobs
                        SET status='processing', attempt_count=attempt_count+1,
                            available_at=%s, error=NULL, updated_at=%s
                        WHERE job_id IN ({placeholders})
                    """, (lease_until, now, *(row["job_id"] for row in rows)))
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        claimed = []
        for row in rows:
            item = self._job(row)
            item.update({
                "status": "processing",
                "attempt_count": int(row["attempt_count"]) + 1,
                "available_at": lease_until.replace(tzinfo=UTC).isoformat(),
                "error": None,
                "updated_at": now.replace(tzinfo=UTC).isoformat(),
            })
            claimed.append(item)
        return claimed

    def complete_job(self, job_id: str) -> bool:
        with self._cursor() as (connection, cursor):
            cursor.execute("""
                UPDATE memory_update_jobs
                SET status='completed', error=NULL, updated_at=%s
                WHERE job_id=%s AND status='processing'
            """, (_now(), job_id))
            connection.commit()
            return bool(cursor.rowcount)

    def fail_job(self, job_id: str, error: str, retry_after_seconds: int,
                 terminal: bool = False) -> bool:
        now = _now()
        with self._cursor() as (connection, cursor):
            cursor.execute("""
                UPDATE memory_update_jobs
                SET status=%s, available_at=%s, error=%s, updated_at=%s
                WHERE job_id=%s AND status='processing'
            """, ('failed' if terminal else 'retry',
                  now + timedelta(seconds=max(0, retry_after_seconds)),
                  str(error)[:2000], now, job_id))
            connection.commit()
            return bool(cursor.rowcount)

    def job_stats(self) -> dict[str, int]:
        with self._cursor() as (_, cursor):
            cursor.execute("""
                SELECT status, COUNT(*) AS count FROM memory_update_jobs GROUP BY status
            """)
            rows = cursor.fetchall()
        stats = {"pending": 0, "processing": 0, "retry": 0,
                 "completed": 0, "failed": 0}
        stats.update({str(row["status"]): int(row["count"]) for row in rows})
        return stats

    def clear_update_jobs(self, user_id: str) -> int:
        with self._cursor() as (connection, cursor):
            cursor.execute("DELETE FROM memory_update_jobs WHERE user_id=%s", (user_id,))
            connection.commit()
            return int(cursor.rowcount)

    def clear_profile(self, user_id: str) -> int:
        with self._cursor() as (connection, cursor):
            cursor.execute("DELETE FROM memory_profiles WHERE user_id=%s", (user_id,))
            connection.commit()
            return int(cursor.rowcount)

    # Query experience ----------------------------------------------------
    @staticmethod
    def _pattern(row: dict[str, Any]) -> dict[str, Any]:
        result = _public_row(row)
        raw = result.pop("strategy_json") or {}
        result["strategy"] = json.loads(raw) if isinstance(raw, str) else raw
        samples = int(result["sample_count"])
        result["success_rate"] = round(int(result["success_count"]) / samples, 4) if samples else 0.0
        return result

    def record(self, event: dict[str, Any]) -> bool:
        now = _now()
        with self._cursor() as (connection, cursor):
            try:
                cursor.execute("""SELECT 1 FROM query_experience_events
                                  WHERE scope_type=%s AND scope_id=%s AND run_id=%s""",
                               (event["scope_type"], event["scope_id"], event["run_id"]))
                if cursor.fetchone():
                    return False
                cursor.execute("""SELECT * FROM query_experience_patterns
                                  WHERE pattern_id=%s FOR UPDATE""", (event["pattern_id"],))
                pattern = cursor.fetchone()
                if pattern:
                    count = int(pattern["sample_count"])
                    quality = (float(pattern["average_quality"]) * count
                               + float(event["quality_score"])) / (count + 1)
                    strategy = (json.dumps(event["strategy"], ensure_ascii=False)
                                if event["eligible"] else pattern["strategy_json"])
                    cursor.execute("""
                        UPDATE query_experience_patterns SET strategy_json=%s,
                            sample_count=%s, success_count=success_count+%s,
                            failure_count=failure_count+%s, average_quality=%s,
                            updated_at=%s WHERE pattern_id=%s
                    """, (strategy, count + 1, int(event["eligible"]),
                          int(not event["eligible"]), round(quality, 6), now,
                          event["pattern_id"]))
                else:
                    cursor.execute("""
                        INSERT INTO query_experience_patterns(
                            pattern_id, scope_type, scope_id, query_template,
                            strategy_json, sample_count, success_count, failure_count,
                            average_quality, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s)
                    """, (event["pattern_id"], event["scope_type"], event["scope_id"],
                          event["query_template"],
                          json.dumps(event["strategy"], ensure_ascii=False),
                          int(event["eligible"]), int(not event["eligible"]),
                          round(float(event["quality_score"]), 6), now, now))
                cursor.execute("""
                    INSERT INTO query_experience_events(
                        event_id, run_id, pattern_id, scope_type, scope_id,
                        normalized_question, query_template, strategy_json, outcome,
                        eligible, validation_pass, quality_score, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (event["event_id"], event["run_id"], event["pattern_id"],
                      event["scope_type"], event["scope_id"], event["normalized_question"],
                      event["query_template"], json.dumps(event["strategy"], ensure_ascii=False),
                      event["outcome"], int(event["eligible"]),
                      int(event["validation_pass"]), float(event["quality_score"]), now))
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def finalize_metrics(self, run_id: str, metrics: dict[str, Any]) -> bool:
        with self._cursor() as (connection, cursor):
            cursor.execute("SELECT pattern_id FROM query_experience_events WHERE run_id=%s",
                           (run_id,))
            patterns = {row["pattern_id"] for row in cursor.fetchall()}
            if not patterns:
                return False
            cursor.execute("""UPDATE query_experience_events
                              SET duration_ms=%s, total_tokens=%s, cost=%s WHERE run_id=%s""",
                           (float(metrics.get("duration_ms") or 0),
                            int(metrics.get("total_tokens") or 0),
                            float(metrics.get("cost") or 0), run_id))
            for pattern_id in patterns:
                cursor.execute("""SELECT AVG(COALESCE(duration_ms, 0)) duration_ms,
                                          AVG(COALESCE(total_tokens, 0)) tokens,
                                          AVG(COALESCE(cost, 0)) cost
                                   FROM query_experience_events WHERE pattern_id=%s""",
                               (pattern_id,))
                aggregate = cursor.fetchone()
                cursor.execute("""UPDATE query_experience_patterns
                                  SET average_duration_ms=%s, average_tokens=%s,
                                      average_cost=%s, updated_at=%s WHERE pattern_id=%s""",
                               (float(aggregate["duration_ms"] or 0),
                                float(aggregate["tokens"] or 0),
                                float(aggregate["cost"] or 0), _now(), pattern_id))
            connection.commit()
        return True

    def list_patterns(self, scope_type: str, scope_id: str, limit: int = 100,
                      positive_only: bool = False) -> list[dict[str, Any]]:
        positive = "AND success_count > 0" if positive_only else ""
        with self._cursor() as (_, cursor):
            cursor.execute(f"""SELECT * FROM query_experience_patterns
                               WHERE scope_type=%s AND scope_id=%s {positive}
                               ORDER BY success_count DESC, updated_at DESC LIMIT %s""",
                           (scope_type, scope_id, max(1, min(limit, 500))))
            return [self._pattern(row) for row in cursor.fetchall()]

    def get_pattern(self, pattern_id: str) -> dict[str, Any] | None:
        with self._cursor() as (_, cursor):
            cursor.execute("SELECT * FROM query_experience_patterns WHERE pattern_id=%s",
                           (pattern_id,))
            row = cursor.fetchone()
        return self._pattern(row) if row else None

    def stats(self, scope_type: str, scope_id: str) -> dict[str, Any]:
        with self._cursor() as (_, cursor):
            cursor.execute("""
                SELECT COUNT(*) pattern_count, COALESCE(SUM(sample_count), 0) event_count,
                       COALESCE(SUM(success_count), 0) success_count,
                       COALESCE(SUM(failure_count), 0) failure_count,
                       COALESCE(AVG(average_quality), 0) average_quality,
                       COALESCE(AVG(average_duration_ms), 0) average_duration_ms,
                       COALESCE(SUM(average_cost * sample_count), 0) total_cost
                FROM query_experience_patterns WHERE scope_type=%s AND scope_id=%s
            """, (scope_type, scope_id))
            row = cursor.fetchone()
        floats = {"average_quality", "average_duration_ms", "total_cost"}
        return {key: round(float(value), 6) if key in floats else int(value)
                for key, value in row.items()}

    def clear_experience_scope(self, scope_type: str,
                               scope_id: str) -> dict[str, int]:
        with self._cursor() as (connection, cursor):
            cursor.execute("""SELECT COUNT(*) count FROM query_experience_events
                              WHERE scope_type=%s AND scope_id=%s""",
                           (scope_type, scope_id))
            events = int(cursor.fetchone()["count"])
            cursor.execute("""SELECT COUNT(*) count FROM query_experience_patterns
                              WHERE scope_type=%s AND scope_id=%s""",
                           (scope_type, scope_id))
            patterns = int(cursor.fetchone()["count"])
            cursor.execute("""DELETE FROM query_experience_events
                              WHERE scope_type=%s AND scope_id=%s""",
                           (scope_type, scope_id))
            cursor.execute("""DELETE FROM query_experience_patterns
                              WHERE scope_type=%s AND scope_id=%s""",
                           (scope_type, scope_id))
            connection.commit()
        return {"deleted_experience_events": events,
                "deleted_experience_patterns": patterns}

    def close(self) -> None:
        # Connections are request-scoped; there is no pool to close.
        return None
