"""Unified MemoryManager contract tests using the SQLite development backend."""

from uuid import uuid4

from services.memory_manager import close_memory_manager, memory_manager


def test_sqlite_manager_unifies_conversation_facts_and_jobs(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORY_BACKEND", "sqlite")
    monkeypatch.setenv("CONVERSATION_MEMORY_DB_PATH", str(tmp_path / "conversation.sqlite"))
    monkeypatch.setenv("QUERY_EXPERIENCE_DB_PATH", str(tmp_path / "experience.sqlite"))
    monkeypatch.setenv("LONG_TERM_MEMORY_DB_PATH", str(tmp_path / "long-term.sqlite"))
    close_memory_manager()
    manager = memory_manager()
    assert manager.backend == "sqlite"

    user_id = "user-memory-contract"
    conversation_id = "conversation-contract"
    run_id = f"run-{uuid4().hex}"
    manager.ensure_conversation(user_id, conversation_id)
    recorded = manager.record_turn(
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
        original_question="请记住我偏好简洁报告。",
        contextualized_question="请记住我偏好简洁报告。",
        final_answer="已了解。",
        intent="偏好设置",
        primary_domain="talent",
        entities=[],
    )
    assert recorded["turn_count"] == 1

    fact = manager.create_fact(
        user_id,
        "用户偏好简洁的专家报告。",
        category="preference",
        confidence=0.9,
        source_run_id=run_id,
        source_conversation_id=conversation_id,
    )
    recalled = manager.recall_context(
        user_id, conversation_id, query="简洁", top_k=5
    )
    assert recalled["conversation"]["turn_count"] == 1
    assert [row["fact_id"] for row in recalled["facts"]] == [fact["fact_id"]]

    updated = manager.update_fact(
        user_id, fact["fact_id"], {"confidence": 0.95}
    )
    assert updated["revision"] == 2
    assert float(updated["confidence"]) == 0.95
    assert manager.enqueue_update(
        user_id, run_id, {"messages": ["test"]}, conversation_id
    ) is True
    assert manager.enqueue_update(
        user_id, run_id, {"messages": ["duplicate"]}, conversation_id
    ) is False
    claimed = manager.claim_update_jobs(limit=10, lease_seconds=30)
    assert len(claimed) == 1
    assert claimed[0]["payload"] == {"messages": ["test"]}
    assert claimed[0]["attempt_count"] == 1
    assert manager.complete_update_job(claimed[0]["job_id"]) is True
    assert manager.update_job_stats()["completed"] == 1
    assert manager.delete_fact(user_id, fact["fact_id"]) is True
    assert manager.search_facts(user_id, "简洁") == []
    close_memory_manager()


def test_manager_facts_are_user_isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORY_BACKEND", "sqlite")
    monkeypatch.setenv("CONVERSATION_MEMORY_DB_PATH", str(tmp_path / "conversation.sqlite"))
    monkeypatch.setenv("QUERY_EXPERIENCE_DB_PATH", str(tmp_path / "experience.sqlite"))
    monkeypatch.setenv("LONG_TERM_MEMORY_DB_PATH", str(tmp_path / "long-term.sqlite"))
    close_memory_manager()
    manager = memory_manager()
    manager.create_fact("user-a", "只属于用户A的长期偏好。", category="preference")
    assert manager.search_facts("user-a", "长期偏好")
    assert manager.search_facts("user-b", "长期偏好") == []
    close_memory_manager()
