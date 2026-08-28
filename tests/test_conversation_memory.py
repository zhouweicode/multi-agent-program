"""Conversation memory API and multi-turn reference resolution tests."""
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from services.conversation_memory import recall_conversation_memory
from tests.helpers import wait_for_run

client = TestClient(app)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _login(client: TestClient, user_id: str, password: str) -> None:
    response = client.post(
        "/auth/login", json={"user_id": user_id, "password": password}
    )
    assert response.status_code == 200


def _complete_ambiguous_first_turn(conversation_id: str, name: str = "张伟",
                                   entity_id: str = "person_zw_001") -> dict:
    run_id = _id("memory-first")
    created = client.post("/queries", json={
        "question": f"{name}发表过哪些论文？",
        "thread_id": run_id,
        "conversation_id": conversation_id,
        "memory_enabled": True,
    })
    assert created.status_code == 202
    waiting = wait_for_run(client, run_id, {"NEED_USER_SELECTION"})
    assert waiting["interrupt"]["candidates"][name]
    resumed = client.post(f"/queries/{run_id}/resume", json={"selections": {name: entity_id}})
    assert resumed.status_code == 202
    return wait_for_run(client, run_id, {"COMPLETED"})


def test_second_turn_resolves_pronoun_to_confirmed_entity():
    conversation_id = _id("conversation")
    first = _complete_ambiguous_first_turn(conversation_id)
    assert first["state"]["conversation_turn_count"] == 1
    assert first["state"]["conversation_entities"][0]["entity_id"] == "person_zw_001"

    second_run = _id("memory-second")
    created = client.post("/queries", json={
        "question": "他任职于哪个机构？",
        "thread_id": second_run,
        "conversation_id": conversation_id,
        "memory_enabled": True,
    })
    assert created.status_code == 202
    completed = wait_for_run(client, second_run, {"COMPLETED"})
    state = completed["state"]
    assert state["original_question"] == "他任职于哪个机构？"
    assert state["contextualized_question"] == "张伟任职于哪个机构？"
    assert state["resolved_entities"] == {"张伟": "person_zw_001"}
    assert state["memory_reference_resolution"]["他"]["entity_id"] == "person_zw_001"
    assert state["conversation_turn_count"] == 2
    assert "清华大学" in state["final_answer"]

    events = client.get(f"/queries/{second_run}/events").json()["events"]
    names = [event["event"] for event in events]
    assert "MEMORY_REFERENCE_RESOLVED" in names
    assert "MEMORY_WRITTEN" in names


def test_memory_disabled_does_not_rewrite_or_seed_entities():
    state = recall_conversation_memory({
        "thread_id": _id("disabled"),
        "question": "他任职于哪个机构？",
        "conversation_id": _id("conversation"),
        "memory_enabled": False,
    })
    assert state["contextualized_question"] == "他任职于哪个机构？"
    assert state["memory_status"] == "DISABLED"
    assert state["memory_reference_resolution"] == {}
    assert "resolved_entities" not in state


def test_ambiguous_pronoun_reuses_existing_human_selection_flow():
    conversation_id = _id("conversation")
    first_run = _id("memory-pair")
    client.post("/queries", json={
        "question": "综合分析张伟和李明的学术和职业合作关系。",
        "thread_id": first_run,
        "conversation_id": conversation_id,
        "memory_enabled": True,
    })
    wait_for_run(client, first_run, {"NEED_USER_SELECTION"})
    client.post(f"/queries/{first_run}/resume", json={
        "selections": {"张伟": "person_zw_001", "李明": "person_lm_001"}
    })
    wait_for_run(client, first_run, {"COMPLETED"})

    second_run = _id("memory-ambiguous")
    client.post("/queries", json={
        "question": "他任职于哪个机构？",
        "thread_id": second_run,
        "conversation_id": conversation_id,
        "memory_enabled": True,
    })
    waiting = wait_for_run(client, second_run, {"NEED_USER_SELECTION"})
    assert waiting["interrupt"]["reason"] == "MEMORY_REFERENCE_AMBIGUOUS"
    assert {row["entity_id"] for row in waiting["interrupt"]["candidates"]["他"]} == {
        "person_zw_001", "person_lm_001"
    }
    client.post(f"/queries/{second_run}/resume", json={"selections": {"他": "person_lm_001"}})
    completed = wait_for_run(client, second_run, {"COMPLETED"})
    assert completed["state"]["contextualized_question"] == "李明任职于哪个机构？"
    assert completed["state"]["resolved_entities"] == {"李明": "person_lm_001"}


def test_clear_memory_removes_turns_and_entities():
    conversation_id = _id("conversation")
    _complete_ambiguous_first_turn(conversation_id)
    before = client.get(f"/conversations/{conversation_id}/memory")
    assert before.status_code == 200
    assert before.json()["turn_count"] == 1
    assert before.json()["entities"]

    cleared = client.delete(f"/conversations/{conversation_id}/memory")
    assert cleared.status_code == 200
    assert cleared.json()["deleted_turns"] == 1
    assert cleared.json()["deleted_entities"] == 1
    after = client.get(f"/conversations/{conversation_id}/memory")
    assert after.status_code == 404


def test_server_generates_conversation_id_when_memory_is_enabled():
    run_id = _id("memory-generated")
    response = client.post("/queries", json={
        "question": "查询人工智能产业链TOP事件。",
        "thread_id": run_id,
        "memory_enabled": True,
    })
    assert response.status_code == 202
    assert response.json()["conversation_id"].startswith("conv-")
    completed = wait_for_run(client, run_id, {"COMPLETED"})
    assert completed["state"]["conversation_turn_count"] == 1


def test_same_conversation_id_is_isolated_between_authenticated_users(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    conversation_id = _id("shared-conversation")
    researcher = TestClient(app)
    analyst = TestClient(app)
    _login(researcher, "user-researcher", "Research@123")
    _login(analyst, "user-analyst", "Analyst@123")

    researcher_run = _id("researcher-memory")
    created = researcher.post("/queries", json={
        "question": "查询人工智能产业链TOP事件。",
        "thread_id": researcher_run,
        "conversation_id": conversation_id,
        "memory_enabled": True,
    })
    assert created.status_code == 202
    wait_for_run(researcher, researcher_run, {"COMPLETED"})

    assert analyst.get(f"/conversations/{conversation_id}/memory").status_code == 404
    assert analyst.delete(f"/conversations/{conversation_id}/memory").status_code == 404
    researcher_memory = researcher.get(f"/conversations/{conversation_id}/memory")
    assert researcher_memory.status_code == 200
    assert researcher_memory.json()["user_id"] == "user-researcher"
    assert researcher_memory.json()["turn_count"] == 1

    analyst_run = _id("analyst-memory")
    created = analyst.post("/queries", json={
        "question": "查询半导体产业链TOP事件。",
        "thread_id": analyst_run,
        "conversation_id": conversation_id,
        "memory_enabled": True,
    })
    assert created.status_code == 202
    wait_for_run(analyst, analyst_run, {"COMPLETED"})

    analyst_memory = analyst.get(f"/conversations/{conversation_id}/memory").json()
    researcher_memory = researcher.get(f"/conversations/{conversation_id}/memory").json()
    assert analyst_memory["user_id"] == "user-analyst"
    assert researcher_memory["user_id"] == "user-researcher"
    assert analyst_memory["recent_turns"][0]["original_question"] == "查询半导体产业链TOP事件。"
    assert researcher_memory["recent_turns"][0]["original_question"] == "查询人工智能产业链TOP事件。"
