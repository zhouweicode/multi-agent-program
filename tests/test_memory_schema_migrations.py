"""Backward-compatible memory schema migration tests."""

import json
import sqlite3

from repositories.conversation_memory_repository import (
    SQLiteConversationMemoryRepository,
)
from repositories.long_term_memory_repository import SQLiteLongTermMemoryRepository
from repositories.query_experience_repository import SQLiteQueryExperienceRepository


def test_legacy_conversation_memory_is_quarantined(tmp_path):
    path = tmp_path / "conversation.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE memory_conversations (
            conversation_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE memory_turns (
            turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            run_id TEXT NOT NULL UNIQUE,
            original_question TEXT NOT NULL,
            contextualized_question TEXT NOT NULL,
            final_answer TEXT,
            intent TEXT,
            primary_domain TEXT,
            resolved_entities_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE memory_entities (
            conversation_id TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            name TEXT NOT NULL,
            organization TEXT,
            title TEXT,
            entity_type TEXT NOT NULL DEFAULT 'scholar',
            mention_count INTEGER NOT NULL DEFAULT 1,
            last_seen_turn INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(conversation_id, entity_id)
        );
    """)
    connection.execute(
        "INSERT INTO memory_conversations VALUES (?, ?, ?)",
        ("legacy-conversation", "2026-01-01", "2026-01-01"),
    )
    connection.execute(
        """INSERT INTO memory_turns(
               conversation_id, run_id, original_question, contextualized_question,
               final_answer, intent, primary_domain, resolved_entities_json, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("legacy-conversation", "legacy-run", "旧问题", "旧问题", "旧答案",
         "事实查询", "talent", json.dumps({"张伟": "person-1"}), "2026-01-01"),
    )
    connection.execute(
        """INSERT INTO memory_entities(
               conversation_id, entity_id, name, entity_type,
               mention_count, last_seen_turn, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("legacy-conversation", "person-1", "张伟", "scholar", 1, 1, "2026-01-01"),
    )
    connection.commit()
    connection.close()

    repository = SQLiteConversationMemoryRepository(str(path))
    try:
        assert repository.exists("legacy-unowned", "legacy-conversation") is True
        assert repository.get("legacy-unowned", "legacy-conversation")["turn_count"] == 1
        assert repository.exists("user-researcher", "legacy-conversation") is False
        assert repository.get("user-researcher", "legacy-conversation")["turn_count"] == 0
    finally:
        repository.close()


def test_legacy_query_experience_is_quarantined_and_redacted(tmp_path):
    path = tmp_path / "experience.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE query_experience_patterns (
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
        CREATE TABLE query_experience_events (
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
            created_at TEXT NOT NULL
        );
    """)
    connection.execute(
        """INSERT INTO query_experience_patterns(
               pattern_id, scope_id, query_template, strategy_json,
               sample_count, success_count, failure_count, average_quality,
               created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("old-pattern", "local", "{SCHOLAR_1}发表过哪些论文", "{}",
         1, 1, 0, 1.0, "2026-01-01", "2026-01-01"),
    )
    connection.execute(
        """INSERT INTO query_experience_events(
               run_id, pattern_id, scope_id, normalized_question, query_template,
               strategy_json, outcome, eligible, validation_pass, quality_score, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("old-run", "old-pattern", "local", "张伟发表过哪些论文",
         "{SCHOLAR_1}发表过哪些论文", "{}", "SUCCESS", 1, 1, 1.0,
         "2026-01-01"),
    )
    connection.commit()
    connection.close()

    repository = SQLiteQueryExperienceRepository(str(path))
    try:
        assert len(repository.list_patterns("legacy", "local")) == 1
        assert repository.list_patterns("global", "local") == []
    finally:
        repository.close()

    connection = sqlite3.connect(path)
    try:
        normalized, template = connection.execute(
            """SELECT normalized_question, query_template
               FROM query_experience_events WHERE event_id='legacy:old-run'"""
        ).fetchone()
        assert normalized == template == "{SCHOLAR_1}发表过哪些论文"
    finally:
        connection.close()


def test_long_term_memory_adds_lifecycle_columns_and_review_date(tmp_path):
    path = tmp_path / "long-term.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE memory_facts (
            fact_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
            agent_name TEXT NOT NULL DEFAULT '', content TEXT NOT NULL,
            normalized_hash TEXT NOT NULL, category TEXT NOT NULL,
            confidence REAL NOT NULL, source_run_id TEXT,
            source_conversation_id TEXT, expected_valid_until TEXT,
            status TEXT NOT NULL DEFAULT 'active', revision INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(user_id, agent_name, normalized_hash)
        );
    """)
    connection.execute(
        """INSERT INTO memory_facts VALUES (
               'fact-old', 'user-old', '', '旧偏好', 'hash-old', 'preference',
               0.9, NULL, NULL, NULL, 'active', 1,
               '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
           )"""
    )
    connection.commit()
    connection.close()

    repository = SQLiteLongTermMemoryRepository(str(path))
    try:
        fact = repository.get("user-old", "fact-old")
        assert fact["recall_count"] == 0
        assert fact["application_count"] == 0
        assert fact["expected_valid_until"].startswith("2026-04-01")
        repository.mark_recalled("user-old", ["fact-old"])
        assert repository.get("user-old", "fact-old")["recall_count"] == 1
    finally:
        repository.close()
