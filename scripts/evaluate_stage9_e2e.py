"""第九阶段端到端离线评测：路由、工具选择、回答覆盖与证据覆盖。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

from graph.builder import build_graph
from langgraph.types import Command


def evaluate(cases: list[dict]) -> dict:
    graph = build_graph()
    rows = []
    for case in cases:
        config = {"configurable": {"thread_id": f"eval-{case['id']}-{uuid4().hex}"}}
        first = graph.invoke({"question": case["question"], "max_replans": 2, "replan_count": 0}, config=config)
        state = (graph.invoke(Command(resume=case["selections"]), config=config)
                 if first.get("__interrupt__") else first)
        result = state.get(f"{case['domain']}_result") or {}
        actual_tools = {fact.get("tool") for fact in result.get("facts", [])}
        expected_tools = set(case.get("tools", []))
        answer = state.get("final_answer", "")
        row = {
            "id": case["id"],
            "routing_pass": state.get("primary_domain") == case["domain"],
            "tool_pass": expected_tools.issubset(actual_tools),
            "answer_pass": all(term in answer for term in case.get("answer_terms", [])),
            "evidence_count": len(state.get("evidence", [])),
            "validation_pass": state.get("validation_result", {}).get("valid", False),
        }
        row["passed"] = all(row[key] for key in ("routing_pass", "tool_pass", "answer_pass", "validation_pass"))
        rows.append(row)
    count = len(rows)
    return {"case_count": count, "passed": sum(row["passed"] for row in rows),
            "pass_rate": round(sum(row["passed"] for row in rows) / count, 4) if count else 0,
            "citation_coverage": round(sum(row["evidence_count"] > 0 for row in rows) / count, 4) if count else 0,
            "cases": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="evals/stage9_end_to_end_cases.json")
    parser.add_argument("--live", action="store_true", help="使用 .env 中的真实模型和数据库；缺省使用可重复的 Mock")
    args = parser.parse_args()
    if not args.live:
        os.environ.update({"MODEL_PROVIDER": "mock", "ENTITY_BACKEND": "mock",
                           "ACHIEVEMENT_BACKEND": "mock", "GRAPH_BACKEND": "mock",
                           "ENTERPRISE_BACKEND": "mock", "INDUSTRY_BACKEND": "mock"})
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    print(json.dumps(evaluate(cases), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
