"""运行产业全景报告 Skill 回归评测。"""
import argparse
import json
import os
from pathlib import Path

from evaluation.industry_landscape_runner import (
    evaluate_industry_landscape_dataset,
    evaluate_industry_landscape_gate,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="evals/industry_landscape_cases.json")
    parser.add_argument("--baseline", default="evals/baselines/industry_landscape_v1.json")
    parser.add_argument("--output", default=".runtime/industry-landscape-eval.json")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--live", action="store_true", help="使用 .env 中的真实模型和数据库")
    args = parser.parse_args()
    if not args.live:
        os.environ.update({
            "MODEL_PROVIDER": "mock", "ENTITY_BACKEND": "mock", "ACHIEVEMENT_BACKEND": "mock",
            "GRAPH_BACKEND": "mock", "ENTERPRISE_BACKEND": "mock", "INDUSTRY_BACKEND": "mock",
            "EMBEDDING_PROVIDER": "mock", "TOOL_TRANSPORT": "local", "WEB_SEARCH_PROVIDER": "disabled",
        })
    report = evaluate_industry_landscape_dataset(args.dataset)
    if args.check:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        report["gate"] = evaluate_industry_landscape_gate(report, baseline)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("case_count", "passed", "metrics", "gate")
                      if key in report}, ensure_ascii=False, indent=2))
    if report.get("gate") and not report["gate"]["passed"]:
        raise SystemExit("产业全景评测门禁失败: " + "; ".join(report["gate"]["failures"]))


if __name__ == "__main__":
    main()
