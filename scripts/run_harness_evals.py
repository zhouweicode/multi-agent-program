"""运行 Harness、循环与工具故障注入评测。"""

import argparse
import json
from pathlib import Path

from evaluation.harness_runner import evaluate_harness_dataset, evaluate_harness_gate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="evals/harness_fault_cases.json")
    parser.add_argument("--baseline", default="evals/baselines/harness_v1.json")
    parser.add_argument("--output", default=".runtime/harness-eval-report.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = evaluate_harness_dataset(args.dataset)
    if args.check:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        report["gate"] = evaluate_harness_gate(report, baseline)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("case_count", "passed", "metrics", "gate")
                if key in report
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if report.get("gate") and not report["gate"]["passed"]:
        raise SystemExit(
            "Harness 评测门禁失败: " + "; ".join(report["gate"]["failures"])
        )


if __name__ == "__main__":
    main()
