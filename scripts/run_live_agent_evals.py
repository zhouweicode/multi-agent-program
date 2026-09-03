"""Run a bounded real-model sample repeatedly; never silently falls back to Mock."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="evals/golden_v1.jsonl")
    parser.add_argument("--case-types", default="routing,workflow")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", default=".runtime/live-agent-eval.json")
    parser.add_argument(
        "--allow-mock", action="store_true",
        help="仅用于验证评测管线；正式报告不要开启",
    )
    args = parser.parse_args()

    from evaluation.live_runner import evaluate_live_dataset

    report = evaluate_live_dataset(
        args.dataset,
        case_types=tuple(
            value.strip() for value in args.case_types.split(",") if value.strip()
        ),
        limit=args.limit,
        repeats=args.repeats,
        allow_mock=args.allow_mock,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "provider": report["provider"],
        "model_name": report["model_name"],
        "case_count": report["case_count"],
        "run_count": report["run_count"],
        "metrics": report["metrics"],
        "output": str(output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
