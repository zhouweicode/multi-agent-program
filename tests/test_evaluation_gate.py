"""黄金集数据契约与 CI 回归门禁。"""
from evaluation.runner import evaluate_gate, load_cases


def test_golden_dataset_has_exactly_50_unique_layered_cases():
    cases = load_cases("evals/golden_v1.jsonl")
    assert len(cases) == 50
    assert {case["case_type"] for case in cases} == {"entity", "routing", "workflow"}
    assert len({case["case_id"] for case in cases}) == 50


def test_regression_gate_blocks_quality_drop():
    baseline = {
        "expected_case_count": 50,
        "metrics": {"routing_accuracy": 1.0},
        "minimums": {"routing_accuracy": 0.95},
        "regression_metrics": ["routing_accuracy"],
    }
    gate = evaluate_gate({"case_count": 50, "metrics": {"routing_accuracy": 0.94}}, baseline)
    assert gate["passed"] is False
    assert any("routing_accuracy" in failure for failure in gate["failures"])
