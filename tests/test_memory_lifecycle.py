"""Stage 5 lifecycle governance, concurrency, audit and shutdown tests."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from models.settings import Settings
from services.memory_admin import list_user_facts
from services.memory_errors import MemoryRevisionConflict
from services.memory_manager import close_memory_manager, memory_manager
from services.memory_update_worker import MemoryUpdateWorker


def _manager(monkeypatch, tmp_path, *, maximum: int = 100,
             similarity: float = 0.86):
    monkeypatch.setenv("MEMORY_BACKEND", "sqlite")
    monkeypatch.setenv("MEMORY_RETRIEVAL_BACKEND", "mysql")
    monkeypatch.setenv("MEMORY_FACT_MAX_PER_SCOPE", str(maximum))
    monkeypatch.setenv("MEMORY_FACT_SIMILARITY_THRESHOLD", str(similarity))
    monkeypatch.setenv("CONVERSATION_MEMORY_DB_PATH", str(tmp_path / "conversation.sqlite"))
    monkeypatch.setenv("QUERY_EXPERIENCE_DB_PATH", str(tmp_path / "experience.sqlite"))
    monkeypatch.setenv("LONG_TERM_MEMORY_DB_PATH", str(tmp_path / "long-term.sqlite"))
    close_memory_manager()
    return memory_manager()


def test_similar_facts_merge_and_contradictions_replace(monkeypatch, tmp_path):
    manager = _manager(monkeypatch, tmp_path)
    first = manager.create_fact(
        "user-life", "以后回答始终使用表格格式", category="output_format",
        confidence=0.9,
    )
    merged = manager.create_fact(
        "user-life", "以后回答始终使用表格格式。", category="output_format",
        confidence=0.95,
    )
    assert merged["fact_id"] == first["fact_id"]
    assert merged["lifecycle_action"] == "merge"
    assert merged["revision"] == 2

    replaced = manager.create_fact(
        "user-life", "以后回答始终使用JSON格式", category="output_format",
        confidence=0.98,
    )
    assert replaced["fact_id"] == first["fact_id"]
    assert replaced["lifecycle_action"] == "replace"
    assert replaced["revision"] == 3
    assert manager.list_facts("user-life", 100)[0]["content"].endswith("JSON格式")
    operations = [row["operation"] for row in manager.list_audit_logs("user-life")]
    assert "fact_merge" in operations
    assert "fact_replace" in operations
    close_memory_manager()


def test_capacity_revision_usage_and_review(monkeypatch, tmp_path):
    manager = _manager(monkeypatch, tmp_path, maximum=10, similarity=0.99)
    protected = manager.create_fact(
        "user-cap", "请记住更正：默认机构是甲单位", category="correction",
        confidence=0.2,
    )
    for index in range(10):
        manager.create_fact(
            "user-cap", f"长期上下文编号 {index} 唯一值 token-{index * 7919}",
            category="context", confidence=0.5 + index / 100,
        )
    facts = manager.list_facts("user-cap", 100)
    assert len(facts) == 10
    assert protected["fact_id"] in {fact["fact_id"] for fact in facts}
    assert "fact_capacity_evicted" in {
        row["operation"] for row in manager.list_audit_logs("user-cap", 100)
    }

    current = facts[0]
    updated = manager.update_fact(
        "user-cap", current["fact_id"], {"confidence": 0.99},
        expected_revision=current["revision"],
    )
    with pytest.raises(MemoryRevisionConflict):
        manager.update_fact(
            "user-cap", current["fact_id"], {"confidence": 0.7},
            expected_revision=current["revision"],
        )
    manager.mark_facts_recalled("user-cap", [updated["fact_id"]])
    manager.mark_facts_applied("user-cap", [updated["fact_id"]])
    measured = next(
        fact for fact in manager.list_facts("user-cap", 100)
        if fact["fact_id"] == updated["fact_id"]
    )
    assert measured["recall_count"] == 1
    assert measured["application_count"] == 1
    assert measured["last_recalled_at"]

    expired = manager.create_fact(
        "user-review", "用户长期关注可解释人工智能", category="focus",
        expected_valid_until=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
    )
    listed = list_user_facts("user-review", manager=manager)
    assert listed[0]["review_status"] == "due"
    renewed = manager.review_fact(
        "user-review", expired["fact_id"], "renew", expired["revision"], 90
    )
    assert renewed["revision"] == 2
    assert list_user_facts("user-review", manager=manager)[0]["review_status"] == "current"
    close_memory_manager()


def test_graceful_worker_stop_flushes_available_jobs(monkeypatch, tmp_path):
    manager = _manager(monkeypatch, tmp_path)
    manager.enqueue_update(
        "user-flush", "run-flush", {"user_message": "以后回答请始终使用表格格式。"}
    )
    worker = MemoryUpdateWorker(
        lambda: manager,
        settings=replace(Settings.from_env(), memory_worker_batch_size=10),
    )
    result = worker.stop(timeout=2, flush=True)
    assert result["claimed"] == 1
    assert result["completed"] == 1
    assert manager.update_job_stats()["completed"] == 1
    assert manager.search_facts("user-flush", "表格")
    close_memory_manager()


def test_api_optimistic_lock_returns_conflict(monkeypatch, tmp_path):
    _manager(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    client = TestClient(app)
    assert client.post("/auth/login", json={
        "user_id": "user-researcher", "password": "Research@123",
    }).status_code == 200
    created = client.post("/memory/facts", json={
        "content": "用户偏好简洁回答", "category": "preference",
    }).json()["fact"]
    first = client.patch(f"/memory/facts/{created['fact_id']}", json={
        "content": "用户偏好简洁的回答", "expected_revision": created["revision"],
    })
    assert first.status_code == 200
    stale = client.patch(f"/memory/facts/{created['fact_id']}", json={
        "content": "用户偏好非常详细的回答",
        "expected_revision": created["revision"],
    })
    assert stale.status_code == 409
    assert client.delete(
        f"/memory/facts/{created['fact_id']}?expected_revision=1"
    ).status_code == 409
    assert client.get("/memory/audit").json()["count"] >= 2
    close_memory_manager()
