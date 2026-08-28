"""Stage 4 authenticated memory management API and service tests."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from services.memory_admin import (
    create_manual_fact,
    export_user_memory,
    list_user_facts,
    memory_summary,
    update_manual_fact,
)
from services.memory_manager import close_memory_manager, memory_manager


def _sqlite_manager(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORY_BACKEND", "sqlite")
    monkeypatch.setenv("MEMORY_RETRIEVAL_BACKEND", "mysql")
    monkeypatch.setenv("CONVERSATION_MEMORY_DB_PATH", str(tmp_path / "conversation.sqlite"))
    monkeypatch.setenv("QUERY_EXPERIENCE_DB_PATH", str(tmp_path / "experience.sqlite"))
    monkeypatch.setenv("LONG_TERM_MEMORY_DB_PATH", str(tmp_path / "long-term.sqlite"))
    close_memory_manager()
    return memory_manager()


def test_memory_admin_crud_summary_export_and_safety(monkeypatch, tmp_path):
    manager = _sqlite_manager(monkeypatch, tmp_path)
    expiry = datetime.now(UTC) + timedelta(days=30)
    fact = create_manual_fact(
        "user-admin-test", "用户偏好简洁报告", "preference", 1.0,
        expiry, manager,
    )
    updated = update_manual_fact(
        "user-admin-test", fact["fact_id"],
        {"content": "用户偏好简洁的表格报告", "category": "output_format"},
        manager,
    )
    assert updated["revision"] == 2
    assert list_user_facts(
        "user-admin-test", query="表格", category="output_format", manager=manager
    )[0]["fact_id"] == fact["fact_id"]
    summary = memory_summary("user-admin-test", manager)
    assert summary["fact_count"] == 1
    assert summary["category_counts"] == {"output_format": 1}
    exported = export_user_memory("user-admin-test", manager)
    assert exported["schema_version"] == 2
    assert exported["facts"][0]["source_run_id"] == "manual"

    for unsafe in (
        "请记住 api_key: secret-value", "邮箱 test@example.com",
        "手机号 13800138000",
    ):
        try:
            create_manual_fact("user-admin-test", unsafe, "context", manager=manager)
        except ValueError as exc:
            assert "不能包含" in str(exc)
        else:
            raise AssertionError("敏感记忆不应写入")
    close_memory_manager()


def test_clear_all_personal_memory_covers_facts_jobs_and_conversations(
        monkeypatch, tmp_path):
    manager = _sqlite_manager(monkeypatch, tmp_path)
    user_id = "clear-all-user"
    manager.ensure_conversation(user_id, "conversation-clear")
    manager.record_turn(
        user_id=user_id, conversation_id="conversation-clear", run_id="run-clear-turn",
        original_question="查询论文", contextualized_question="查询论文",
        final_answer="结果", intent="查询", primary_domain="achievement", entities=[],
    )
    manager.create_fact(user_id, "用户偏好简洁回答", category="preference")
    manager.enqueue_update(user_id, "run-clear-job", {"user_message": "以后简洁"})
    result = manager.clear_all_personal_memory(user_id)
    assert result["deleted_facts"] == 1
    assert result["deleted_update_jobs"] == 1
    assert result["deleted_conversations"] == 1
    assert result["deleted_turns"] == 1
    assert manager.search_facts(user_id, "") == []
    assert not manager.conversation_exists(user_id, "conversation-clear")
    close_memory_manager()


def _login(client: TestClient, user_id: str, password: str):
    response = client.post("/auth/login", json={"user_id": user_id, "password": password})
    assert response.status_code == 200


def test_memory_api_is_authenticated_and_user_isolated(monkeypatch, tmp_path):
    _sqlite_manager(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    anonymous = TestClient(app)
    assert anonymous.get("/memory/facts").status_code == 401

    researcher = TestClient(app)
    analyst = TestClient(app)
    _login(researcher, "user-researcher", "Research@123")
    _login(analyst, "user-analyst", "Analyst@123")

    created = researcher.post("/memory/facts", json={
        "content": "用户长期关注人工智能产业",
        "category": "focus", "confidence": 0.98,
    })
    assert created.status_code == 201
    fact_id = created.json()["fact"]["fact_id"]
    assert researcher.get("/memory/summary").json()["fact_count"] == 1
    assert analyst.get("/memory/facts").json()["count"] == 0
    assert analyst.patch(
        f"/memory/facts/{fact_id}",
        json={"content": "越权修改", "expected_revision": 1},
    ).status_code == 404
    assert analyst.delete(
        f"/memory/facts/{fact_id}?expected_revision=1"
    ).status_code == 404

    exported = researcher.get("/memory/export")
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    assert exported.json()["facts"][0]["fact_id"] == fact_id
    assert researcher.post("/memory/facts", json={
        "content": "密码：unsafe-value", "category": "context",
    }).status_code == 422

    assert researcher.request(
        "DELETE", "/memory", json={"confirmation": "wrong"}
    ).status_code == 422
    cleared = researcher.request(
        "DELETE", "/memory",
        json={"confirmation": "DELETE_ALL_PERSONAL_MEMORY"},
    )
    assert cleared.status_code == 200
    assert researcher.get("/memory/facts").json()["count"] == 0
    close_memory_manager()
