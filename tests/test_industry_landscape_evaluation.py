import json
from pathlib import Path

from evaluation.industry_landscape_runner import (
    evaluate_industry_landscape_dataset,
    evaluate_industry_landscape_gate,
)


def test_industry_landscape_evaluation_passes_baseline():
    report = evaluate_industry_landscape_dataset("evals/industry_landscape_cases.json")
    baseline = json.loads(Path("evals/baselines/industry_landscape_v1.json").read_text(encoding="utf-8"))
    assert report["case_count"] == 4
    assert report["metrics"]["case_pass_rate"] == 1
    assert evaluate_industry_landscape_gate(report, baseline) == {"passed": True, "failures": []}
