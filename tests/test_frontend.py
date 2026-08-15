"""前端入口和执行事件 API 契约测试。"""
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_frontend_index_and_assets_are_served():
    page = client.get("/")
    assert page.status_code == 200
    assert "GraphRAG Studio" in page.text
    assert "NODE INSPECTOR" in page.text
    assert client.get("/static/styles.css").status_code == 200
    layout_css = client.get("/static/layout-fix.css")
    assert layout_css.status_code == 200
    assert "overflow-wrap: anywhere" in layout_css.text
    assert "grid-column: 1 / -1" in layout_css.text
    assert client.get("/static/app.js").status_code == 200


def test_event_api_exposes_query_execution_trace():
    thread_id = "frontend-event-trace"
    response = client.post("/queries", json={
        "question": "查询人工智能产业链TOP事件。",
        "thread_id": thread_id,
        "max_replans": 2,
    })
    assert response.status_code == 200
    events = client.get(f"/queries/{thread_id}/events").json()
    names = [item["event"] for item in events["events"]]
    assert names[0] == "QUERY_STARTED"
    assert "ROUTER_COMPLETED" in names
    assert "AGENT_TOOL_CALLED" in names
    assert "RULE_VALIDATION_COMPLETED" in names
    assert "ANSWER_GENERATED" in names
    assert names[-1] == "NODE_EXECUTED"
    node_events = [item for item in events["events"] if item["event"] == "NODE_EXECUTED"]
    assert {item["node_name"] for item in node_events} == {
        "router", "entity_resolution", "industry_agent", "merge", "validator", "answer"
    }
    assert all("node_input" in item and "node_output" in item for item in node_events)


def test_event_api_supports_incremental_cursor():
    thread_id = "frontend-event-cursor"
    client.post("/queries", json={"question": "查询人工智能产业链TOP事件。", "thread_id": thread_id})
    first = client.get(f"/queries/{thread_id}/events").json()
    cursor = first["events"][1]["sequence"]
    later = client.get(f"/queries/{thread_id}/events?after={cursor}").json()
    assert all(item["sequence"] > cursor for item in later["events"])
    assert later["cursor"] >= cursor


def test_entity_resolution_trace_records_interrupt_and_resumed_output():
    thread_id = "frontend-node-interrupt-detail"
    created = client.post("/queries", json={"question": "张伟发表过哪些论文？", "thread_id": thread_id})
    assert created.json()["status"] == "NEED_USER_SELECTION"
    before_resume = client.get(f"/queries/{thread_id}/events").json()["events"]
    interrupted = [item for item in before_resume if item["event"] == "NODE_INTERRUPTED"]
    assert interrupted[-1]["node_name"] == "entity_resolution"
    assert interrupted[-1]["node_input"]["entity_mentions"] == ["张伟"]

    resumed = client.post(f"/queries/{thread_id}/resume", json={"selections": {"张伟": "person_zw_001"}})
    assert resumed.json()["status"] == "COMPLETED"
    after_resume = client.get(f"/queries/{thread_id}/events").json()["events"]
    completed = [item for item in after_resume
                 if item["event"] == "NODE_EXECUTED" and item["node_name"] == "entity_resolution"]
    assert completed[-1]["node_output"]["resolved_entities"] == {"张伟": "person_zw_001"}
