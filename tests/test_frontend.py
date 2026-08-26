"""前端入口和执行事件 API 契约测试。"""
from fastapi.testclient import TestClient

from app.main import app
from tests.helpers import wait_for_run

client = TestClient(app)


def test_frontend_index_and_assets_are_served():
    page = client.get("/")
    assert page.status_code == 200
    assert "GraphRAG Studio" in page.text
    assert "NODE INSPECTOR" in page.text
    assert 'id="submitLabel"' in page.text
    assert "专利成果" in page.text
    assert "跨域画像" in page.text
    assert "成果对比" in page.text
    assert "共同成果" in page.text
    assert "产企关联" in page.text
    assert "间接关系" in page.text
    assert "综合验证" in page.text
    assert "联网研究" in page.text
    assert 'data-agent="web_research_agent"' in page.text
    assert 'id="webSearchToggle"' in page.text
    assert 'id="webSourcesPanel"' in page.text
    assert "联网搜索：已开启" in page.text
    assert "深圳科技大学003的高芳" in page.text
    assert "上海科技大学002的赵强" in page.text
    assert "李明" not in page.text
    assert '/static/app.js?v=20260826-3' in page.text
    assert 'id="runComparePanel"' in page.text
    assert 'id="leftRunSelect"' in page.text
    assert 'id="rightRunSelect"' in page.text
    assert page.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert client.get("/static/styles.css").status_code == 200
    layout_css = client.get("/static/layout-fix.css")
    assert layout_css.status_code == 200
    assert "overflow-wrap: anywhere" in layout_css.text
    assert "grid-column: 1 / -1" in layout_css.text
    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert script.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert "停止分析" in script.text
    assert "/cancel" in script.text
    assert "WebResearchAgent" in script.text
    assert "web_search_enabled:state.webSearchEnabled" in script.text
    assert "renderWebSources" in script.text
    assert "/observability/compare" in script.text
    assert "loadRunOptions" in script.text


def test_query_api_persists_web_search_switch():
    thread_id = "frontend-web-switch-off"
    response = client.post("/queries", json={
        "question": "查询人工智能产业链最新新闻并联网查证。",
        "thread_id": thread_id,
        "web_search_enabled": False,
    })
    assert response.status_code == 202
    completed = wait_for_run(client, thread_id, {"COMPLETED"})
    assert completed["state"]["web_search_enabled"] is False
    assert completed["state"].get("web_result") is None
    assert completed["state"]["industry_result"]["agent"] == "industry_agent"


def test_event_api_exposes_query_execution_trace():
    thread_id = "frontend-event-trace"
    response = client.post("/queries", json={
        "question": "查询人工智能产业链TOP事件。",
        "thread_id": thread_id,
        "max_replans": 2,
    })
    assert response.status_code == 202
    wait_for_run(client, thread_id, {"COMPLETED"})
    events = client.get(f"/queries/{thread_id}/events").json()
    names = [item["event"] for item in events["events"]]
    assert names[0] == "QUERY_STARTED"
    assert "ROUTER_COMPLETED" in names
    assert "AGENT_TOOL_CALLED" in names
    assert "RULE_VALIDATION_COMPLETED" in names
    assert "ANSWER_GENERATED" in names
    assert names[-1] == "RUN_STATUS_CHANGED"
    node_events = [item for item in events["events"] if item["event"] == "NODE_EXECUTED"]
    assert {item["node_name"] for item in node_events} == {
        "router", "entity_resolution", "industry_agent", "merge", "validator", "answer"
    }
    assert all("node_input" in item and "node_output" in item for item in node_events)


def test_event_api_supports_incremental_cursor():
    thread_id = "frontend-event-cursor"
    client.post("/queries", json={"question": "查询人工智能产业链TOP事件。", "thread_id": thread_id})
    wait_for_run(client, thread_id, {"COMPLETED"})
    first = client.get(f"/queries/{thread_id}/events").json()
    cursor = first["events"][1]["sequence"]
    later = client.get(f"/queries/{thread_id}/events?after={cursor}").json()
    assert all(item["sequence"] > cursor for item in later["events"])
    assert later["cursor"] >= cursor


def test_entity_resolution_trace_records_interrupt_and_resumed_output():
    thread_id = "frontend-node-interrupt-detail"
    created = client.post("/queries", json={"question": "张伟发表过哪些论文？", "thread_id": thread_id})
    assert created.status_code == 202
    waiting = wait_for_run(client, thread_id, {"NEED_USER_SELECTION"})
    assert waiting["status"] == "NEED_USER_SELECTION"
    before_resume = client.get(f"/queries/{thread_id}/events").json()["events"]
    interrupted = [item for item in before_resume if item["event"] == "NODE_INTERRUPTED"]
    assert interrupted[-1]["node_name"] == "entity_resolution"
    assert interrupted[-1]["node_input"]["entity_mentions"] == ["张伟"]

    resumed = client.post(f"/queries/{thread_id}/resume", json={"selections": {"张伟": "person_zw_001"}})
    assert resumed.status_code == 202
    assert wait_for_run(client, thread_id, {"COMPLETED"})["status"] == "COMPLETED"
    after_resume = client.get(f"/queries/{thread_id}/events").json()["events"]
    completed = [item for item in after_resume
                 if item["event"] == "NODE_EXECUTED" and item["node_name"] == "entity_resolution"]
    assert completed[-1]["node_output"]["resolved_entities"] == {"张伟": "person_zw_001"}


def test_sse_stream_contains_trace_and_terminal_status():
    run_id = "frontend-sse-stream"
    client.post("/queries", json={"question": "查询人工智能产业链TOP事件。", "thread_id": run_id})
    wait_for_run(client, run_id, {"COMPLETED"})
    response = client.get(f"/queries/{run_id}/stream")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: trace" in response.text
    assert "event: status" in response.text
