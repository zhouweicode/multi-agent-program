"""SQLite conversation memory repository.

Conversation memory is operational state, not knowledge-graph truth.  It is
stored separately from KG data and can be deleted independently by the user.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteConversationMemoryRepository:
    LEGACY_OWNER_ID = "legacy-unowned"

    def __init__(self, path: str):
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = Lock()
        self._migrate_legacy_schema()
        with self._connection:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS memory_conversations (
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, conversation_id)
                );
                CREATE TABLE IF NOT EXISTS memory_turns (
                    turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    original_question TEXT NOT NULL,
                    contextualized_question TEXT NOT NULL,
                    final_answer TEXT,
                    intent TEXT,
                    primary_domain TEXT,
                    resolved_entities_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id, conversation_id)
                        REFERENCES memory_conversations(user_id, conversation_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_memory_turns_conversation
                    ON memory_turns(user_id, conversation_id, turn_id DESC);
                CREATE TABLE IF NOT EXISTS memory_entities (
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    organization TEXT,
                    title TEXT,
                    entity_type TEXT NOT NULL DEFAULT 'scholar',
                    mention_count INTEGER NOT NULL DEFAULT 1,
                    last_seen_turn INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, conversation_id, entity_id),
                    FOREIGN KEY(user_id, conversation_id)
                        REFERENCES memory_conversations(user_id, conversation_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_memory_entities_focus
                    ON memory_entities(
                        user_id, conversation_id, last_seen_turn DESC, mention_count DESC
                    );
            """)

    def _table_columns(self, table_name: str) -> set[str]:
        return {
            str(row["name"])
            for row in self._connection.execute(
                f'PRAGMA table_info("{table_name}")'
            ).fetchall()
        }

    def _migrate_legacy_schema(self) -> None:
        """Upgrade the pre-user-scope schema without assigning data to a real user.

        Historical rows have no trustworthy owner information. They are retained
        under a sentinel bucket so an authenticated user cannot accidentally read
        or delete another user's old conversation.
        """
        columns = self._table_columns("memory_conversations")
        if not columns or "user_id" in columns:
            return

        owner = self.LEGACY_OWNER_ID
        self._connection.execute("PRAGMA foreign_keys = OFF")
        try:
            with self._connection:
                self._connection.executescript("""
                    CREATE TABLE memory_conversations_v2 (
                        user_id TEXT NOT NULL,
                        conversation_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(user_id, conversation_id)
                    );
                    CREATE TABLE memory_turns_v2 (
                        turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        conversation_id TEXT NOT NULL,
                        run_id TEXT NOT NULL UNIQUE,
                        original_question TEXT NOT NULL,
                        contextualized_question TEXT NOT NULL,
                        final_answer TEXT,
                        intent TEXT,
                        primary_domain TEXT,
                        resolved_entities_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(user_id, conversation_id)
                            REFERENCES memory_conversations_v2(user_id, conversation_id)
                            ON DELETE CASCADE
                    );
                    CREATE TABLE memory_entities_v2 (
                        user_id TEXT NOT NULL,
                        conversation_id TEXT NOT NULL,
                        entity_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        organization TEXT,
                        title TEXT,
                        entity_type TEXT NOT NULL DEFAULT 'scholar',
                        mention_count INTEGER NOT NULL DEFAULT 1,
                        last_seen_turn INTEGER NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(user_id, conversation_id, entity_id),
                        FOREIGN KEY(user_id, conversation_id)
                            REFERENCES memory_conversations_v2(user_id, conversation_id)
                            ON DELETE CASCADE
                    );
                """)
                self._connection.execute("""
                    INSERT INTO memory_conversations_v2(
                        user_id, conversation_id, created_at, updated_at
                    )
                    SELECT ?, conversation_id, created_at, updated_at
                    FROM memory_conversations
                """, (owner,))
                self._connection.execute("""
                    INSERT INTO memory_turns_v2(
                        turn_id, user_id, conversation_id, run_id,
                        original_question, contextualized_question, final_answer,
                        intent, primary_domain, resolved_entities_json, created_at
                    )
                    SELECT turn_id, ?, conversation_id, run_id,
                           original_question, contextualized_question, final_answer,
                           intent, primary_domain, resolved_entities_json, created_at
                    FROM memory_turns
                """, (owner,))
                self._connection.execute("""
                    INSERT INTO memory_entities_v2(
                        user_id, conversation_id, entity_id, name, organization,
                        title, entity_type, mention_count, last_seen_turn, updated_at
                    )
                    SELECT ?, conversation_id, entity_id, name, organization,
                           title, entity_type, mention_count, last_seen_turn, updated_at
                    FROM memory_entities
                """, (owner,))
                self._connection.executescript("""
                    DROP TABLE memory_entities;
                    DROP TABLE memory_turns;
                    DROP TABLE memory_conversations;
                    ALTER TABLE memory_conversations_v2 RENAME TO memory_conversations;
                    ALTER TABLE memory_turns_v2 RENAME TO memory_turns;
                    ALTER TABLE memory_entities_v2 RENAME TO memory_entities;
                """)
        finally:
            self._connection.execute("PRAGMA foreign_keys = ON")

    def ensure_conversation(self, user_id: str, conversation_id: str) -> None:
        now = _now()
        with self._lock, self._connection:
            self._connection.execute("""
                INSERT INTO memory_conversations(
                    user_id, conversation_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, conversation_id)
                DO UPDATE SET updated_at=excluded.updated_at
            """, (user_id, conversation_id, now, now))

    def record_turn(self, user_id: str, conversation_id: str, run_id: str, original_question: str,
                    contextualized_question: str, final_answer: str | None,
                    intent: str | None, primary_domain: str | None,
                    entities: list[dict[str, Any]]) -> dict[str, Any]:
        """Persist one completed turn and update its focused entities atomically."""
        now = _now()
        with self._lock, self._connection:
            self._connection.execute("""
                INSERT INTO memory_conversations(
                    user_id, conversation_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, conversation_id)
                DO UPDATE SET updated_at=excluded.updated_at
            """, (user_id, conversation_id, now, now))
            cursor = self._connection.execute("""
                INSERT INTO memory_turns(
                    user_id, conversation_id, run_id, original_question, contextualized_question,
                    final_answer, intent, primary_domain, resolved_entities_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO NOTHING
            """, (user_id, conversation_id, run_id, original_question, contextualized_question,
                  final_answer, intent, primary_domain,
                  json.dumps({row["name"]: row["entity_id"] for row in entities}, ensure_ascii=False), now))
            if cursor.rowcount:
                turn_id = int(cursor.lastrowid)
                for entity in entities:
                    self._connection.execute("""
                        INSERT INTO memory_entities(
                            user_id, conversation_id, entity_id, name, organization, title,
                            entity_type, mention_count, last_seen_turn, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                        ON CONFLICT(user_id, conversation_id, entity_id) DO UPDATE SET
                            name=excluded.name,
                            organization=COALESCE(excluded.organization, memory_entities.organization),
                            title=COALESCE(excluded.title, memory_entities.title),
                            entity_type=excluded.entity_type,
                            mention_count=memory_entities.mention_count + 1,
                            last_seen_turn=excluded.last_seen_turn,
                            updated_at=excluded.updated_at
                    """, (user_id, conversation_id, entity["entity_id"], entity["name"],
                          entity.get("organization"), entity.get("title"),
                          entity.get("entity_type", "scholar"), turn_id, now))
        return self.get(user_id, conversation_id)

    def exists(self, user_id: str, conversation_id: str) -> bool:
        with self._lock:
            row = self._connection.execute("""
                SELECT 1 FROM memory_conversations
                WHERE user_id=? AND conversation_id=?
            """, (user_id, conversation_id)).fetchone()
        return row is not None

    def get(self, user_id: str, conversation_id: str, turn_limit: int = 10) -> dict[str, Any]:
        with self._lock:
            conversation = self._connection.execute(
                """SELECT * FROM memory_conversations
                   WHERE user_id=? AND conversation_id=?""",
                (user_id, conversation_id),
            ).fetchone()
            if not conversation:
                return {"user_id": user_id, "conversation_id": conversation_id,
                        "turn_count": 0, "entities": [], "turns": []}
            entities = self._connection.execute("""
                SELECT entity_id, name, organization, title, entity_type, mention_count,
                       last_seen_turn, updated_at
                FROM memory_entities WHERE user_id=? AND conversation_id=?
                ORDER BY last_seen_turn DESC, mention_count DESC, entity_id
            """, (user_id, conversation_id)).fetchall()
            turns = self._connection.execute("""
                SELECT run_id, original_question, contextualized_question, intent,
                       primary_domain, resolved_entities_json, created_at
                FROM memory_turns WHERE user_id=? AND conversation_id=?
                ORDER BY turn_id DESC LIMIT ?
            """, (user_id, conversation_id, max(1, min(turn_limit, 50)))).fetchall()
            count = self._connection.execute(
                """SELECT COUNT(*) FROM memory_turns
                   WHERE user_id=? AND conversation_id=?""",
                (user_id, conversation_id),
            ).fetchone()[0]
        return {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "created_at": conversation["created_at"],
            "updated_at": conversation["updated_at"],
            "turn_count": int(count),
            "entities": [dict(row) for row in entities],
            "turns": [dict(row) | {"resolved_entities": json.loads(row["resolved_entities_json"] or "{}")}
                      for row in turns],
        }

    def clear(self, user_id: str, conversation_id: str) -> dict[str, int | str | bool]:
        with self._lock, self._connection:
            turn_count = int(self._connection.execute(
                """SELECT COUNT(*) FROM memory_turns
                   WHERE user_id=? AND conversation_id=?""",
                (user_id, conversation_id),
            ).fetchone()[0])
            entity_count = int(self._connection.execute(
                """SELECT COUNT(*) FROM memory_entities
                   WHERE user_id=? AND conversation_id=?""",
                (user_id, conversation_id),
            ).fetchone()[0])
            self._connection.execute(
                "DELETE FROM memory_conversations WHERE user_id=? AND conversation_id=?",
                (user_id, conversation_id),
            )
        return {"user_id": user_id, "conversation_id": conversation_id, "cleared": True,
                "deleted_turns": turn_count, "deleted_entities": entity_count}

    def clear_user_conversations(self, user_id: str) -> dict[str, int]:
        with self._lock, self._connection:
            conversation_count = int(self._connection.execute(
                "SELECT COUNT(*) FROM memory_conversations WHERE user_id=?",
                (user_id,),
            ).fetchone()[0])
            turn_count = int(self._connection.execute(
                "SELECT COUNT(*) FROM memory_turns WHERE user_id=?", (user_id,)
            ).fetchone()[0])
            entity_count = int(self._connection.execute(
                "SELECT COUNT(*) FROM memory_entities WHERE user_id=?", (user_id,)
            ).fetchone()[0])
            self._connection.execute(
                "DELETE FROM memory_conversations WHERE user_id=?", (user_id,)
            )
        return {"deleted_conversations": conversation_count,
                "deleted_turns": turn_count, "deleted_entities": entity_count}

    def close(self) -> None:
        with self._lock:
            self._connection.close()
