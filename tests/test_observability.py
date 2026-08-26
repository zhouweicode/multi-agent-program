"""统一 Trace、Token/成本统计与双 Run 对比 API。"""
from __future__ import annotations

import time
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app.main import app
from mcp_runtime.client import MCPGateway
from mcp_runtime.server import mcp
from services.telemetry import (
    TelemetryCallbackHandler,
    activate_trace,
    finish_run_trace,
    prepare_run_trace,
    traced_span,
)
from tests.helpers import wait_for_run


client = TestClient(app)


def _wait_for_trace(run_id: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/observability/runs/{run_id}")
        if response.status_code == 200 and response.json()["status"] != "RUNNING":
            return response.json()
        time.sleep(0.01)
    raise AssertionError(f"Trace {run_id} 未结束")


def _completed_query(suffix: str) -> tuple[str, dict]:
    run_id = f"observability-{suffix}-{uuid4().hex[:8]}"
    created = client.post("/queries", json={
        "question": "查询人工智能产业链TOP事件。", "thread_id": run_id, "web_search_enabled": False,
    })
    assert created.status_code == 202
    assert created.json()["trace_id"]
    wait_for_run(client, run_id, {"COMPLETED"})
    return run_id, _wait_for_trace(run_id)


def test_trace_connects_api_graph_agent_tool_and_events():
    run_id, trace = _completed_query("full-chain")
    names = {span["name"] for span in trace["spans"]}
    assert {"api.queries.create", "graphrag.workflow.execute", "langgraph.node.router",
            "agent.industry_agent", "tool.get_node_events"}.issubset(names)
    assert trace["summary"]["tool_calls"] == 4
    assert trace["summary"]["tool_successes"] == 4
    assert trace["summary"]["error_count"] == 0
    events = client.get(f"/queries/{run_id}/events").json()["events"]
    assert all(item.get("trace_id") for item in events)
    assert {item["trace_id"] for item in events} == {trace["attempts"][0]["trace_id"]}
    summary = client.get("/observability/summary").json()
    assert {"p95_duration_ms", "timeout_rate", "error_rate", "average_replans",
            "tool_success_rate", "average_cost"}.issubset(summary)


def test_token_cost_accounting_and_summary(monkeypatch):
    monkeypatch.setenv("MODEL_INPUT_COST_PER_MILLION", "1")
    monkeypatch.setenv("MODEL_OUTPUT_COST_PER_MILLION", "2")
    run_id = f"observability-cost-{uuid4().hex[:8]}"
    context = prepare_run_trace(run_id, {"workflow_version": "cost-test"})
    with activate_trace(context):
        with traced_span("gen_ai.test", "model") as span:
            span.set_usage(input_tokens=10, output_tokens=20)
    finish_run_trace(context, "COMPLETED")
    trace = client.get(f"/observability/runs/{run_id}").json()
    assert trace["summary"]["input_tokens"] == 10
    assert trace["summary"]["output_tokens"] == 20
    assert trace["summary"]["total_tokens"] == 30
    assert trace["summary"]["cost"] == 0.00005


def test_langchain_callback_records_provider_usage_across_callback_context(monkeypatch):
    monkeypatch.setenv("MODEL_INPUT_COST_PER_MILLION", "1")
    monkeypatch.setenv("MODEL_OUTPUT_COST_PER_MILLION", "2")
    run_id = f"observability-callback-{uuid4().hex[:8]}"
    callback_id = uuid4()
    context = prepare_run_trace(run_id, {"workflow_version": "callback-test"})
    handler = TelemetryCallbackHandler()
    with activate_trace(context):
        handler.on_chat_model_start({}, [], run_id=callback_id,
                                    invocation_params={"model_name": "test-model"})
    response = LLMResult(generations=[[ChatGeneration(message=AIMessage(
        content="ok", usage_metadata={"input_tokens": 30, "output_tokens": 10, "total_tokens": 40},
    ))]])
    handler.on_llm_end(response, run_id=callback_id)
    finish_run_trace(context, "COMPLETED")
    trace = client.get(f"/observability/runs/{run_id}").json()
    assert trace["summary"]["total_tokens"] == 40
    assert trace["summary"]["cost"] == 0.00005
    assert trace["spans"][0]["attributes"]["gen_ai.model"] == "test-model"


def test_mcp_continues_remote_trace_context():
    run_id = f"observability-mcp-{uuid4().hex[:8]}"
    context = prepare_run_trace(run_id, {"tool_transport": "mcp"})
    with activate_trace(context):
        result = MCPGateway(mcp).call_tool("get_author_papers", {"entity_id": "person_zw_001"})
    finish_run_trace(context, "COMPLETED")
    assert result
    trace = client.get(f"/observability/runs/{run_id}").json()
    names = {span["name"] for span in trace["spans"]}
    assert "mcp.client.get_author_papers" in names
    assert "mcp.server.get_author_papers" in names


def test_compare_api_returns_metric_and_span_diff():
    left_id, left = _completed_query("compare-left")
    right_id, right = _completed_query("compare-right")
    response = client.get("/observability/compare", params={
        "left_run_id": left_id, "right_run_id": right_id,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["left"]["run_id"] == left["run_id"]
    assert payload["right"]["run_id"] == right["run_id"]
    assert "duration_ms" in payload["delta"]
    assert set(payload["span_diff"]) == {"only_left", "only_right"}


def test_entity_resume_is_aggregated_as_two_attempts():
    run_id = f"observability-resume-{uuid4().hex[:8]}"
    created = client.post("/queries", json={"question": "张伟发表过哪些论文？", "thread_id": run_id})
    assert created.status_code == 202
    wait_for_run(client, run_id, {"NEED_USER_SELECTION"})
    _wait_for_trace(run_id)
    resumed = client.post(f"/queries/{run_id}/resume", json={"selections": {"张伟": "person_zw_001"}})
    assert resumed.status_code == 202
    wait_for_run(client, run_id, {"COMPLETED"})
    trace = _wait_for_trace(run_id)
    assert trace["summary"]["attempt_count"] == 2
    assert trace["status"] == "COMPLETED"
    listed = client.get("/observability/runs?limit=100").json()["runs"]
    row = next(item for item in listed if item["run_id"] == run_id)
    assert row["attempt_count"] == 2
    assert row["tool_calls"] == trace["summary"]["tool_calls"]
