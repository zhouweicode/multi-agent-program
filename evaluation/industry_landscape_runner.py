"""产业全景报告 Skill、证据绑定与联网故障降级评测。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4


def load_industry_landscape_cases(path: str | Path) -> list[dict[str, Any]]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise TypeError("产业全景评测集必须是 JSON 数组")
    ids = [row.get("case_id") for row in rows]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("产业全景评测 case_id 必须存在且唯一")
    return rows


def _workflow(case: dict[str, Any]) -> dict[str, Any]:
    from graph.builder import build_graph

    payload = case["input"]
    state = build_graph().invoke({
        "question": payload["question"], "web_search_enabled": False,
        "max_replans": 2, "replan_count": 0, "resolved_entities": {}, "task_history": [],
    }, config={"configurable": {"thread_id": f"industry-eval-{case['case_id']}-{uuid4().hex}"}})
    report = state.get("report_draft", {})
    catalog_ids = {item.get("evidence_id") for item in report.get("evidence_catalog", [])}
    claims = [claim for section in report.get("sections", []) for claim in section.get("claims", [])]
    return {
        "agents": [task["agent"] for task in state.get("tasks", [])],
        "sections": [section["section_id"] for section in report.get("sections", [])],
        "report_type": report.get("report_type"),
        "top_n_companies": state.get("skill_input", {}).get("top_n_companies"),
        "top_n_events": state.get("skill_input", {}).get("top_n_events"),
        "evidence_coverage": report.get("evidence_coverage"),
        "validation_valid": state.get("validation_result", {}).get("valid"),
        "citations_valid": bool(claims) and all(
            claim.get("evidence_ids") and set(claim["evidence_ids"]) <= catalog_ids for claim in claims
        ),
        "has_markdown_report": state.get("final_answer", "").startswith("# "),
    }


def _optional_failure(_case: dict[str, Any]) -> dict[str, Any]:
    from nodes.validator_node import validator_node

    result = validator_node({
        "requested_skill": "industry_landscape", "skill_required_domains": ["industry"],
        "complexity": "complex", "resolved_entities": {},
        "tasks": [{"task_id": "skill_industry_landscape_web", "agent": "web_research_agent",
                   "required_fact_types": ["web_sources"], "required_entity_ids": []}],
        "industry_result": {"agent": "industry_agent", "errors": [], "facts": [
            {"tool": "search_industry_segments", "data": [{"segment_id": "node_model"}]},
            {"tool": "get_chain_structure", "data": {"chain_id": "chain_ai", "node_details": []}},
        ]},
        "web_result": {"agent": "web_research_agent", "facts": [], "evidence": [],
                       "errors": ["[TOOL_TIMEOUT] injected web timeout"]},
        "evidence": [],
    })["validation_result"]
    return {"validation_valid": result["valid"], "needs_replan": result["needs_replan"],
            "has_warning": bool(result["warnings"])}


def _input_guard(case: dict[str, Any]) -> dict[str, Any]:
    from skills.industry_landscape.spec import normalize_industry_landscape_input

    payload = case["input"]
    return normalize_industry_landscape_input(payload["question"], {
        "top_n_companies": payload["top_n_companies"], "top_n_events": payload["top_n_events"],
    })


_EVALUATORS = {"workflow": _workflow, "optional_failure": _optional_failure, "input_guard": _input_guard}


def evaluate_industry_landscape_dataset(path: str | Path) -> dict[str, Any]:
    rows = []
    for case in load_industry_landscape_cases(path):
        scenario = case["scenario"]
        if scenario not in _EVALUATORS:
            raise ValueError(f"未知产业全景评测场景: {scenario}")
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


def evaluate_industry_landscape_gate(report: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    failures = []
    if report["case_count"] != baseline.get("expected_case_count"):
        failures.append(f"评测数量应为 {baseline.get('expected_case_count')}，实际为 {report['case_count']}")
    minimum = baseline.get("minimum_case_pass_rate", 1)
    if report["metrics"]["case_pass_rate"] < minimum:
        failures.append(f"case_pass_rate 低于门槛 {minimum}")
    return {"passed": not failures, "failures": failures}
