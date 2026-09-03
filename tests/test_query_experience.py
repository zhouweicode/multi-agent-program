"""Query experience shadow recall, distillation and API tests."""
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from models.schemas import PlannedTask, SupervisorPlan
from nodes.supervisor_node import _apply_experience_advice
from services.memory_manager import memory_manager
from services.query_experience import (
    query_template,
    recall_query_experience,
    write_query_experience,
)
from tests.helpers import wait_for_run

client = TestClient(app)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _successful_state(question: str, name: str, run_id: str) -> dict:
    return {
        "user_id": "local-user",
        "thread_id": run_id,
        "question": question,
        "experience_memory_enabled": True,
        "intent": "事实查询",
        "complexity": "simple",
        "primary_domain": "achievement",
        "requires_verification": False,
        "entity_mentions": [name],
        "resolved_entities": {name: f"entity-{name}"},
        "achievement_result": {
            "agent": "achievement_agent",
            "tool_calls": [{"name": "get_author_papers", "arguments": {}}],
            "errors": [],
        },
        "evidence": [{"evidence_id": "ev-1"}],
        "validation_result": {"valid": True},
        "final_answer": "已返回经过校验的论文结果。",
    }


def test_query_template_reuses_strategy_across_different_entities():
    left = query_template("张伟发表过哪些论文？", ["张伟"])
    right = query_template("李明发表过哪些论文？", ["李明"])
    assert left == right == "{SCHOLAR_1}发表过哪些论文"


def test_successful_experience_is_recalled_in_shadow_mode():
    marker = uuid4().hex[:10]
    first = _successful_state(f"张伟发表过哪些{marker}论文？", "张伟", _id("experience-write"))
    written = write_query_experience(first)
    assert written["experience_writeback_status"] == "WRITTEN"
    assert written["experience_pattern"]["success_count"] == 1

    recalled = recall_query_experience({
        "user_id": "local-user",
        "thread_id": _id("experience-recall"),
        "question": f"李明发表过哪些{marker}论文？",
        "entity_mentions": ["李明"],
        "intent": "事实查询",
        "complexity": "simple",
        "primary_domain": "achievement",
        "experience_memory_enabled": True,
    })
    assert recalled["experience_recall_status"] == "HIT"
    assert recalled["experience_match"]["similarity"] == 1.0
    assert recalled["experience_match"]["route_agreement"] is True
    assert recalled["experience_match"]["applicable"] is False
    assert recalled["experience_strategy"]["agents"] == ["achievement_agent"]
    assert recalled["experience_strategy"]["tools_by_agent"] == {
        "achievement_agent": ["get_author_papers"]
    }


def test_negative_experience_is_stored_but_not_recommended():
    marker = uuid4().hex
    state = _successful_state(f"张伟的{marker}失败查询", "张伟", _id("experience-negative"))
    state["validation_result"] = {"valid": False}
    state["achievement_result"]["errors"] = ["tool failed"]
    written = write_query_experience(state)
    assert written["experience_pattern"]["success_count"] == 0
    assert written["experience_pattern"]["failure_count"] == 1

    recalled = recall_query_experience({
        "user_id": "local-user",
        "thread_id": _id("experience-negative-recall"),
        "question": f"李明的{marker}失败查询",
        "entity_mentions": ["李明"],
        "complexity": "simple",
        "primary_domain": "achievement",
        "experience_memory_enabled": True,
    })
    assert recalled["experience_recall_status"] == "MISS"


def test_disabled_experience_memory_neither_recalls_nor_writes():
    state = _successful_state("张伟发表过哪些论文？", "张伟", _id("experience-disabled"))
    state["experience_memory_enabled"] = False
    recalled = recall_query_experience(state)
    written = write_query_experience(state)
    assert recalled["experience_recall_status"] == "DISABLED"
    assert written["experience_writeback_status"] == "DISABLED"


def test_repeated_api_query_hits_experience_and_exposes_stats():
    marker = uuid4().hex[:12]
    question = f"{marker}查询人工智能产业链TOP事件。"
    first_run = _id("experience-api-first")
    first = client.post("/queries", json={
        "question": question, "thread_id": first_run,
        "experience_memory_enabled": True,
    })
    assert first.status_code == 202
    first_result = wait_for_run(client, first_run, {"COMPLETED"})
    assert first_result["state"]["experience_writeback_status"] == "WRITTEN"

    second_run = _id("experience-api-second")
    second = client.post("/queries", json={
        "question": question, "thread_id": second_run,
        "experience_memory_enabled": True,
    })
    assert second.status_code == 202
    second_result = wait_for_run(client, second_run, {"COMPLETED"})
    assert second_result["state"]["experience_recall_status"] == "HIT"
    assert second_result["state"]["experience_match"]["similarity"] == 1.0
    assert second_result["state"]["experience_pattern"]["sample_count"] == 2

    events = client.get(f"/queries/{second_run}/events").json()["events"]
    assert "EXPERIENCE_RECALL_HIT" in [event["event"] for event in events]
    assert "EXPERIENCE_WRITTEN" in [event["event"] for event in events]
    stats = client.get("/experience-memory/stats")
    assert stats.status_code == 200
    assert stats.json()["event_count"] >= 2
    patterns = client.get("/experience-memory/patterns?limit=10")
    assert patterns.status_code == 200
    assert patterns.json()["patterns"]


def test_private_experience_is_isolated_and_safe_success_is_shared_globally():
    marker = f"跨用户安全模板{uuid4().hex}"
    researcher_state = _successful_state(
        f"张伟发表过哪些{marker}论文？", "张伟", _id("experience-global")
    )
    researcher_state["user_id"] = "user-researcher"
    written = write_query_experience(researcher_state)
    assert written["experience_writeback_status"] == "WRITTEN"
    assert written["experience_global_writeback_status"] == "WRITTEN"

    recalled = recall_query_experience({
        "user_id": "user-analyst",
        "thread_id": _id("experience-global-recall"),
        "question": f"李明发表过哪些{marker}论文？",
        "entity_mentions": ["李明"],
        "intent": "事实查询",
        "complexity": "simple",
        "primary_domain": "achievement",
        "experience_memory_enabled": True,
    })
    assert recalled["experience_recall_status"] == "HIT"
    assert recalled["experience_match"]["scope_type"] == "global"

    sensitive_marker = "privateonly" + "".join(
        chr(ord("a") + int(character, 16)) for character in uuid4().hex
    )
    private_state = _successful_state(
        f"张伟的api_key：{sensitive_marker}专属查询", "张伟", _id("experience-private")
    )
    private_state["user_id"] = "user-researcher"
    private_written = write_query_experience(private_state)
    assert private_written["experience_writeback_status"] == "WRITTEN"
    assert private_written["experience_global_writeback_status"] == "SKIPPED"

    manager = memory_manager()
    researcher_patterns = manager.list_experience_patterns(
        "user", "user-researcher", 500
    )
    analyst_patterns = manager.list_experience_patterns("user", "user-analyst", 500)
    assert any(sensitive_marker in row["query_template"] for row in researcher_patterns)
    assert not any(sensitive_marker in row["query_template"] for row in analyst_patterns)


def test_advisory_and_active_experience_only_apply_whitelisted_hints(monkeypatch):
    plan = SupervisorPlan(tasks=[
        PlannedTask(task_id="talent", agent="talent_agent", goal="任职"),
        PlannedTask(task_id="achievement", agent="achievement_agent", goal="论文"),
    ], reason="fresh plan")
    state = {
        "experience_match": {"applicable": True, "pattern_id": "exp-1"},
        "experience_route_agreement": True,
        "experience_strategy": {
            "agents": ["achievement_agent", "talent_agent"],
            "tools_by_agent": {
                "achievement_agent": ["get_author_papers", "delete_database"],
                "talent_agent": ["get_person_profile"],
            },
        },
    }
    monkeypatch.setenv("QUERY_EXPERIENCE_MODE", "advisory")
    advised = _apply_experience_advice(plan, state, False)
    assert [task.agent for task in advised.tasks] == [
        "talent_agent", "achievement_agent",
    ]
    assert advised.tasks[1].preferred_tools == ["get_author_papers"]

    monkeypatch.setenv("QUERY_EXPERIENCE_MODE", "active")
    active = _apply_experience_advice(plan, state, False)
    assert [task.agent for task in active.tasks] == [
        "achievement_agent", "talent_agent",
    ]
    assert "delete_database" not in {
        name for task in active.tasks for name in task.preferred_tools
    }
