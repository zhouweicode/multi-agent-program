import json
from pathlib import Path

from evaluation.harness_runner import evaluate_harness_dataset, evaluate_harness_gate


def test_harness_fault_injection_evaluation_passes_baseline():
    report = evaluate_harness_dataset("evals/harness_fault_cases.json")
    baseline = json.loads(
        Path("evals/baselines/harness_v1.json").read_text(encoding="utf-8")
    )
    assert report["case_count"] == 6
    assert report["metrics"]["case_pass_rate"] == 1
    assert evaluate_harness_gate(report, baseline) == {"passed": True, "failures": []}
