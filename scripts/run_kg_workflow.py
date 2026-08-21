"""Run or resume the durable Phase-1 KG build workflow."""
from __future__ import annotations

import argparse
import json

from kg_workflow.pipeline import KGWorkflow
from kg_workflow.incremental import IncrementalKGWorkflow
from kg_workflow.registry import KGWorkflowRegistry


def main() -> None:
    parser = argparse.ArgumentParser(description="运行可恢复的 Phase-1 图谱构建 Workflow")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--run-id", help="恢复失败运行时复用原 run_id")
    parser.add_argument("--run-type", choices=("SNAPSHOT", "INCREMENTAL"), default="SNAPSHOT")
    parser.add_argument("--registry", default=".runtime/kg-workflow.sqlite")
    parser.add_argument("--artifact-root", default=".runtime/kg-workflow")
    parser.add_argument("--apply", action="store_true", help="通过质量门禁后写入并激活 release")
    args = parser.parse_args()
    registry = KGWorkflowRegistry(args.registry)
    try:
        workflow_class = IncrementalKGWorkflow if args.run_type == "INCREMENTAL" else KGWorkflow
        result = workflow_class(registry=registry, artifact_root=args.artifact_root).start(
            release_id=args.release_id, run_id=args.run_id, run_type=args.run_type, apply=args.apply,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        registry.close()


if __name__ == "__main__":
    main()
