"""Stage 2 passive long-term memory extraction and worker tests."""

from dataclasses import replace

from models.settings import Settings
from services.long_term_memory import (
    ExtractionResult,
    build_memory_update_payload,
    extract_long_term_facts,
)
from services.memory_manager import close_memory_manager, memory_manager
from services.memory_update_worker import MemoryUpdateWorker


def _manager(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORY_BACKEND", "sqlite")
    monkeypatch.setenv("CONVERSATION_MEMORY_DB_PATH", str(tmp_path / "conversation.sqlite"))
    monkeypatch.setenv("QUERY_EXPERIENCE_DB_PATH", str(tmp_path / "experience.sqlite"))
    monkeypatch.setenv("LONG_TERM_MEMORY_DB_PATH", str(tmp_path / "long-term.sqlite"))
    close_memory_manager()
    return memory_manager()


def test_extractor_allows_only_explicit_durable_user_statements():
    result = extract_long_term_facts({
        "user_message": (
            "请记住我偏好简洁的Markdown报告；"
            "我长期关注人工智能产业；"
            "查询张伟2024年的三篇论文"
        ),
        "assistant_response": "模型推测张伟最擅长知识图谱。",
    })
    assert [(fact.category, fact.content) for fact in result.facts] == [
        ("output_format", "请记住我偏好简洁的Markdown报告"),
        ("focus", "我长期关注人工智能产业"),
    ]
    assert result.rejected_count == 1
    assert all("模型推测" not in fact.content for fact in result.facts)


def test_sensitive_source_is_redacted_and_fails_closed():
    payload = build_memory_update_payload({
        "question": "请记住 api_key: ultra-secret-token，我手机号是13800138000",
        "final_answer": "已记住。",
    })
    serialized = str(payload)
    assert "ultra-secret-token" not in serialized
    assert "13800138000" not in serialized
    assert payload["contains_sensitive_data"] is True
    result = extract_long_term_facts(payload)
    assert result.sensitive is True
    assert result.facts == []


def test_worker_claims_writes_and_deduplicates(monkeypatch, tmp_path):
    manager = _manager(monkeypatch, tmp_path)
    settings = replace(
        Settings.from_env(),
        memory_worker_batch_size=10,
        memory_worker_lease_seconds=30,
        memory_worker_max_attempts=3,
    )
    payload = {"user_message": "以后回答请始终使用表格格式。"}
    assert manager.enqueue_update("user-a", "run-1", payload, "conversation-a")
    worker = MemoryUpdateWorker(lambda: manager, settings=settings)
    processed = worker.process_once()
    assert processed == {"claimed": 1, "completed": 1, "retried": 0,
                         "failed": 0, "facts_written": 1}
    facts = manager.search_facts("user-a", "表格")
    assert len(facts) == 1
    assert facts[0]["category"] == "output_format"
    assert facts[0]["source_run_id"] == "run-1"
    assert manager.update_job_stats()["completed"] == 1

    # A different run with identical content completes but upserts one fact.
    assert manager.enqueue_update("user-a", "run-2", payload, "conversation-a")
    worker.process_once()
    assert len(manager.search_facts("user-a", "表格")) == 1
    close_memory_manager()


def test_worker_retries_then_marks_terminal_failure(monkeypatch, tmp_path):
    manager = _manager(monkeypatch, tmp_path)
    settings = replace(
        Settings.from_env(),
        memory_worker_batch_size=1,
        memory_worker_lease_seconds=5,
        memory_worker_max_attempts=2,
    )
    assert manager.enqueue_update("user-a", "run-fail", {"user_message": "请记住偏好"})

    def broken_extractor(_payload):
        raise RuntimeError("expected extractor failure")

    worker = MemoryUpdateWorker(lambda: manager, broken_extractor, settings)
    first = worker.process_once()
    assert first["retried"] == 1
    assert manager.update_job_stats()["retry"] == 1

    # Retry delay is durable; claim it after making this test job immediately available.
    repository = manager.long_term
    with repository._lock, repository._connection:
        repository._connection.execute(
            "UPDATE memory_update_jobs SET available_at='2000-01-01T00:00:00+00:00'"
        )
    second = worker.process_once()
    assert second["failed"] == 1
    assert manager.update_job_stats()["failed"] == 1
    close_memory_manager()


def test_empty_extraction_completes_without_writing(monkeypatch, tmp_path):
    manager = _manager(monkeypatch, tmp_path)
    assert manager.enqueue_update(
        "user-a", "run-once", {"user_message": "查询今年的三篇论文"}
    )
    worker = MemoryUpdateWorker(
        lambda: manager,
        lambda _payload: ExtractionResult([], rejected_count=1),
        Settings.from_env(),
    )
    result = worker.process_once()
    assert result["completed"] == 1
    assert result["facts_written"] == 0
    assert manager.search_facts("user-a", "") == []
    close_memory_manager()
