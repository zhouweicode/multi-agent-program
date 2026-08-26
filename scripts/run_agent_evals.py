"""运行黄金集并可按基线执行 CI 回归门禁。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _configure_mock() -> None:
    os.environ.update({
        "MODEL_PROVIDER": "mock", "ENTITY_BACKEND": "mock", "ACHIEVEMENT_BACKEND": "mock",
        "GRAPH_BACKEND": "mock", "ENTERPRISE_BACKEND": "mock", "INDUSTRY_BACKEND": "mock",
        "EMBEDDING_PROVIDER": "mock", "TOOL_TRANSPORT": "local", "WEB_SEARCH_PROVIDER": "disabled",
    })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="evals/golden_v1.jsonl")
    parser.add_argument("--baseline", default="evals/baselines/agentops_v1.json")
    parser.add_argument("--output", default=".runtime/eval-report.json")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-regression", type=float, default=0.02)
    args = parser.parse_args()
    if not args.live:
        _configure_mock()

    from evaluation.runner import evaluate_dataset, evaluate_gate

    report = evaluate_dataset(args.dataset)
    gate = None
    if args.check:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        gate = evaluate_gate(report, baseline, args.max_regression)
        report["gate"] = gate
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"case_count": report["case_count"], "passed": report["passed"],
                      "metrics": report["metrics"], "gate": gate}, ensure_ascii=False, indent=2))
    if gate and not gate["passed"]:
        raise SystemExit("评测回归门禁失败: " + "; ".join(gate["failures"]))


if __name__ == "__main__":
    main()
