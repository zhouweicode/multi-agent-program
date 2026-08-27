"""专家报告 Skill、证据绑定与可选域故障降级评测。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4


def load_expert_report_cases(path: str | Path) -> list[dict[str, Any]]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise TypeError("专家报告评测集必须是 JSON 数组")
    ids = [row.get("case_id") for row in rows]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("专家报告评测 case_id 必须存在且唯一")
    return rows


def _workflow(case: dict[str, Any]) -> dict[str, Any]:
    from graph.builder import build_graph
    from langgraph.types import Command

    payload = case["input"]
    graph = build_graph()
    config = {"configurable": {"thread_id": f"expert-eval-{case['case_id']}-{uuid4().hex}"}}
    state = graph.invoke({
        "question": payload["question"], "web_search_enabled": False,
        "max_replans": 2, "replan_count": 0, "resolved_entities": {}, "task_history": [],
    }, config=config)
    if state.get("__interrupt__"):
        state = graph.invoke(Command(resume=payload["selections"]), config=config)
    report = state.get("report_draft", {})
    catalog_ids = {item.get("evidence_id") for item in report.get("evidence_catalog", [])}
    claims = [claim for section in report.get("sections", []) for claim in section.get("claims", [])]
    citations_valid = bool(claims) and all(
        claim.get("evidence_ids") and set(claim["evidence_ids"]) <= catalog_ids for claim in claims
    )
    return {
        "agents": [task["agent"] for task in state.get("tasks", [])],
        "sections": [section["section_id"] for section in report.get("sections", [])],
        "evidence_coverage": report.get("evidence_coverage"),
        "validation_valid": state.get("validation_result", {}).get("valid"),
        "citations_valid": citations_valid,
        "has_markdown_report": state.get("final_answer", "").startswith("# "),
    }


def _optional_failure(_case: dict[str, Any]) -> dict[str, Any]:
    from nodes.validator_node import validator_node

    result = validator_node({
        "requested_skill": "expert_report",
        "skill_required_domains": ["talent", "achievement"],
        "complexity": "complex",
        "resolved_entities": {"张伟": "person_zw_001"},
        "tasks": [{
            "task_id": "skill_expert_report_graph", "agent": "graph_reasoning_agent",
            "required_fact_types": ["neighbors"], "required_entity_ids": ["person_zw_001"],
        }],
        "graph_result": {"agent": "graph_reasoning_agent", "facts": [], "evidence": [],
                         "errors": ["[TOOL_TIMEOUT] injected graph timeout"]},
        "evidence": [],
    })["validation_result"]
    return {"validation_valid": result["valid"], "needs_replan": result["needs_replan"],
            "has_warning": bool(result["warnings"])}


def _input_guard(case: dict[str, Any]) -> dict[str, Any]:
    from skills.expert_report.spec import normalize_expert_report_input

    return normalize_expert_report_input(case["input"]["question"], {"top_n": case["input"]["top_n"]})


_EVALUATORS = {"workflow": _workflow, "optional_failure": _optional_failure, "input_guard": _input_guard}


def evaluate_expert_report_dataset(path: str | Path) -> dict[str, Any]:
    rows = []
    for case in load_expert_report_cases(path):
        scenario = case["scenario"]
        if scenario not in _EVALUATORS:
            raise ValueError(f"未知专家报告评测场景: {scenario}")
        actual = _EVALUATORS[scenario](case)
        expected = case["expected"]
        checks = {key: actual.get(key) == value for key, value in expected.items()}
        if scenario == "workflow":
            checks.update({"citations_valid": actual["citations_valid"],
                           "has_markdown_report": actual["has_markdown_report"]})
        rows.append({"case_id": case["case_id"], "scenario": scenario,
                     "passed": all(checks.values()), "checks": checks,
                     "expected": expected, "actual": actual})
    passed = sum(row["passed"] for row in rows)
    return {"dataset": str(path), "case_count": len(rows), "passed": passed,
            "metrics": {"case_pass_rate": round(passed / len(rows), 4) if rows else None}, "cases": rows}


def evaluate_expert_report_gate(report: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    failures = []
    if report["case_count"] != baseline.get("expected_case_count"):
        failures.append(f"评测数量应为 {baseline.get('expected_case_count')}，实际为 {report['case_count']}")
    minimum = baseline.get("minimum_case_pass_rate", 1)
    if report["metrics"]["case_pass_rate"] < minimum:
        failures.append(f"case_pass_rate 低于门槛 {minimum}")
    return {"passed": not failures, "failures": failures}
