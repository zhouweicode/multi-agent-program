import json
from pathlib import Path

from evaluation.expert_report_runner import evaluate_expert_report_dataset, evaluate_expert_report_gate


def test_expert_report_evaluation_passes_baseline():
    report = evaluate_expert_report_dataset("evals/expert_report_cases.json")
    baseline = json.loads(Path("evals/baselines/expert_report_v1.json").read_text(encoding="utf-8"))
    assert report["case_count"] == 4
    assert report["metrics"]["case_pass_rate"] == 1
    assert evaluate_expert_report_gate(report, baseline) == {"passed": True, "failures": []}
