import json

import pytest

from evaluation.live_runner import evaluate_live_dataset, summarize_live_runs


def test_live_evaluation_refuses_silent_mock_fallback(monkeypatch, tmp_path):
    dataset = tmp_path / "live.jsonl"
    dataset.write_text(json.dumps({
        "case_id": "route-1", "case_type": "routing",
        "input": {"question": "test"}, "expected": {},
    }), encoding="utf-8")
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    with pytest.raises(ValueError, match="拒绝 MODEL_PROVIDER=mock"):
        evaluate_live_dataset(dataset)


def test_live_summary_reports_repeat_consistency_and_runtime_failures():
    rows = [
        {
            "case_id": "workflow-1", "case_type": "workflow", "passed": True,
            "actual_agents": ["talent_agent"], "actual_tools": ["get_person_profile"],
            "validation": {"valid": True}, "agent_stop_reasons": ["completed"],
            "duration_ms": 100.0, "replan_count": 0, "agent_run_count": 1,
            "invalid_tool_call_count": 0, "incomplete_agent_count": 0,
        },
        {
            "case_id": "workflow-1", "case_type": "workflow", "passed": False,
            "actual_agents": ["talent_agent"], "actual_tools": [],
            "validation": {"valid": False}, "agent_stop_reasons": ["AGENT_NO_PROGRESS"],
            "duration_ms": 180.0, "replan_count": 1, "agent_run_count": 1,
            "invalid_tool_call_count": 0, "incomplete_agent_count": 1,
        },
    ]
    report = summarize_live_runs(
        rows, provider="openai", model_name="glm-test", repeats=2
    )
    assert report["metrics"]["workflow_consistency"] == 0.5
    assert report["metrics"]["tool_plan_consistency"] == 0.5
    assert report["metrics"]["case_pass_rate"] == 0.5
    assert report["metrics"]["incomplete_agent_rate"] == 0.5
    assert report["metrics"]["no_progress_stop_rate"] == 0.5


def test_live_evaluation_records_case_exception_and_continues(monkeypatch, tmp_path):
    dataset = tmp_path / "live.jsonl"
    dataset.write_text("\n".join(json.dumps({
        "case_id": f"route-{index}", "case_type": "routing",
        "input": {"question": "test"}, "expected": {},
    }) for index in (1, 2)), encoding="utf-8")
    monkeypatch.setenv("MODEL_PROVIDER", "mock")

    def broken(_case):
        raise TimeoutError("model deadline")

    report = evaluate_live_dataset(
        dataset, case_types=("routing",), limit=2, repeats=2,
        allow_mock=True, evaluator=broken,
    )
    assert report["run_count"] == 4
    assert report["passed"] == 0
    assert report["metrics"]["routing_consistency"] == 0.0
    assert all("TimeoutError" in row["evaluation_error"] for row in report["cases"])
