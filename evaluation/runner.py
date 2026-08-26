"""分层黄金集评测：实体消歧、路由规划与完整 GraphRAG 工作流。"""
from __future__ import annotations

import json
import math
import time
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import uuid4


QUALITY_METRICS = (
    "entity_recall_at_10",
    "entity_auto_precision",
    "routing_accuracy",
    "verification_routing_accuracy",
    "task_routing_accuracy",
    "tool_call_accuracy",
    "evidence_completeness",
    "citation_validity",
    "answer_accuracy",
    "validation_pass_rate",
    "case_pass_rate",
)


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"黄金集第 {line_number} 行不是合法 JSON: {exc}") from exc
    ids = [row.get("case_id") for row in rows]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("黄金集 case_id 必须存在且唯一")
    return rows


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return round(float(numerator) / float(denominator), 4) if denominator else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 3)


def _evaluate_entity(case: dict[str, Any]) -> dict[str, Any]:
    from services.resources import get_entity_service

    expected = case["expected"]
    rows = get_entity_service().search(case["input"]["mention"], case["input"].get("context", ""))[:10]
    ids = [row["entity_id"] for row in rows]
    predicted = get_entity_service().auto_resolve(rows)
    expected_id = expected.get("entity_id")
    not_found = expected.get("status") == "ENTITY_NOT_FOUND"
    recall_hit = bool(expected_id and expected_id in ids)
    not_found_hit = bool(not_found and not rows)
    auto_eligible = "auto_match" in expected
    auto_correct = predicted == expected.get("auto_match") if auto_eligible else None
    passed = (recall_hit if expected_id else not_found_hit) and (auto_correct is not False)
    return {
        "case_id": case["case_id"], "case_type": "entity", "passed": passed,
        "recall_hit": recall_hit, "not_found_hit": not_found_hit,
        "auto_eligible": auto_eligible, "auto_predicted": predicted, "auto_correct": auto_correct,
        "candidate_ids": ids,
    }


def _evaluate_routing(case: dict[str, Any]) -> dict[str, Any]:
    from nodes.router_node import router_node

    payload = case["input"]
    expected = case["expected"]
    actual = router_node({
        "question": payload["question"],
        "web_search_enabled": payload.get("web_search_enabled", True),
    })
    checks = {
        key: actual.get(key) == value
        for key, value in expected.items()
        if key in {"primary_domain", "complexity", "requires_verification", "entity_mentions"}
    }
    return {
        "case_id": case["case_id"], "case_type": "routing", "passed": all(checks.values()),
        "checks": checks, "expected": expected, "actual": actual,
    }


def _invoke_workflow(case: dict[str, Any]) -> dict[str, Any]:
    from graph.builder import build_graph
    from langgraph.types import Command

    graph = build_graph()
    config = {"configurable": {"thread_id": f"golden-{case['case_id']}-{uuid4().hex}"}}
    payload = case["input"]
    state = graph.invoke({
        "question": payload["question"],
        "web_search_enabled": payload.get("web_search_enabled", False),
        "max_replans": payload.get("max_replans", 2),
        "replan_count": 0,
        "resolved_entities": {},
        "task_history": [],
    }, config=config)
    if state.get("__interrupt__"):
        state = graph.invoke(Command(resume=payload.get("selections", {})), config=config)
    return state


def _evaluate_workflow(case: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    state = _invoke_workflow(case)
    duration_ms = (time.perf_counter() - started) * 1000
    expected = case["expected"]
    tasks = state.get("tasks", [])
    actual_agents = {task["agent"] for task in tasks}
    actual_tools: set[str] = set()
    for domain in ("talent", "achievement", "enterprise", "industry", "graph", "web"):
        result = state.get(f"{domain}_result") or {}
        if result.get("agent"):
            actual_agents.add(result["agent"])
        actual_tools.update(fact.get("tool") for fact in result.get("facts", []) if fact.get("tool"))
    expected_agents = set(expected.get("agents", []))
    expected_tools = set(expected.get("tools", []))
    answer = state.get("final_answer", "")
    evidence = state.get("evidence", [])
    complete_fields = ("evidence_id", "fact_type", "source_name", "source_record_id", "source_tool")
    complete_evidence = sum(all(item.get(field) not in (None, "") for field in complete_fields) for item in evidence)
    valid_citations = sum(
        all(item.get(field) not in (None, "") for field in ("evidence_id", "source_name", "source_record_id"))
        and item.get("source_type") not in (None, "unknown")
        for item in evidence
    )
    checks = {
        "primary_domain": state.get("primary_domain") == expected.get("primary_domain"),
        "agents": actual_agents == expected_agents,
        "tools": actual_tools == expected_tools,
        "answer": all(term in answer for term in expected.get("answer_contains", [])),
        "validation": state.get("validation_result", {}).get("valid") == expected.get("validation_valid", True),
    }
    return {
        "case_id": case["case_id"], "case_type": "workflow", "passed": all(checks.values()),
        "checks": checks, "actual_agents": sorted(actual_agents), "actual_tools": sorted(actual_tools),
        "expected_agents": sorted(expected_agents), "expected_tools": sorted(expected_tools),
        "evidence_count": len(evidence), "complete_evidence": complete_evidence,
        "valid_citations": valid_citations, "duration_ms": round(duration_ms, 3),
        "replan_count": int(state.get("replan_count", 0) or 0),
        "validation": state.get("validation_result", {}),
    }


def evaluate_dataset(path: str | Path) -> dict[str, Any]:
    cases = load_cases(path)
    evaluators = {"entity": _evaluate_entity, "routing": _evaluate_routing, "workflow": _evaluate_workflow}
    rows = []
    for case in cases:
        case_type = case.get("case_type")
        if case_type not in evaluators:
            raise ValueError(f"未知 case_type: {case_type}")
        rows.append(evaluators[case_type](case))

    counts = Counter(row["case_type"] for row in rows)
    entities = [row for row in rows if row["case_type"] == "entity"]
    routings = [row for row in rows if row["case_type"] == "routing"]
    workflows = [row for row in rows if row["case_type"] == "workflow"]
    auto_rows = [row for row in entities if row["auto_eligible"]]
    verification_checks = [row["checks"]["requires_verification"] for row in routings
                           if "requires_verification" in row["checks"]]
    evidence_total = sum(row["evidence_count"] for row in workflows)
    metrics = {
        "entity_recall_at_10": _ratio(sum(row["recall_hit"] for row in entities),
                                       sum(bool(row["candidate_ids"]) and not row["not_found_hit"] for row in entities)),
        "entity_auto_precision": _ratio(sum(row["auto_correct"] is True for row in auto_rows), len(auto_rows)),
        "entity_not_found_accuracy": _ratio(sum(row["not_found_hit"] for row in entities),
                                             sum(row["not_found_hit"] or not row["candidate_ids"] for row in entities)),
        "routing_accuracy": _ratio(sum(row["checks"].get("primary_domain", True) for row in routings), len(routings)),
        "verification_routing_accuracy": _ratio(sum(verification_checks), len(verification_checks)),
        "task_routing_accuracy": _ratio(sum(row["checks"]["agents"] for row in workflows), len(workflows)),
        "tool_call_accuracy": _ratio(sum(row["checks"]["tools"] for row in workflows), len(workflows)),
        "evidence_completeness": _ratio(sum(row["complete_evidence"] for row in workflows), evidence_total),
        "citation_validity": _ratio(sum(row["valid_citations"] for row in workflows), evidence_total),
        "answer_accuracy": _ratio(sum(row["checks"]["answer"] for row in workflows), len(workflows)),
        "validation_pass_rate": _ratio(sum(row["checks"]["validation"] for row in workflows), len(workflows)),
        "case_pass_rate": _ratio(sum(row["passed"] for row in rows), len(rows)),
        "p95_latency_ms": _percentile([row["duration_ms"] for row in workflows], 0.95),
        "average_latency_ms": round(mean(row["duration_ms"] for row in workflows), 3) if workflows else None,
        "average_replans": round(mean(row["replan_count"] for row in workflows), 4) if workflows else None,
        "average_cost": 0.0,
    }
    return {
        "dataset": str(path), "case_count": len(rows), "case_type_counts": dict(counts),
        "passed": sum(row["passed"] for row in rows), "metrics": metrics, "cases": rows,
    }


def evaluate_gate(report: dict[str, Any], baseline: dict[str, Any], max_regression: float = 0.02) -> dict[str, Any]:
    metrics = report["metrics"]
    failures = []
    for name, minimum in baseline.get("minimums", {}).items():
        value = metrics.get(name)
        if value is None or value < minimum:
            failures.append(f"{name}={value} 低于门槛 {minimum}")
    for name, maximum in baseline.get("maximums", {}).items():
        value = metrics.get(name)
        if value is None or value > maximum:
            failures.append(f"{name}={value} 高于门槛 {maximum}")
    for name in baseline.get("regression_metrics", QUALITY_METRICS):
        value = metrics.get(name)
        previous = baseline.get("metrics", {}).get(name)
        if value is not None and previous is not None and value < previous - max_regression:
            failures.append(f"{name} 从 {previous} 回退到 {value}，超过容忍值 {max_regression}")
    expected_count = baseline.get("expected_case_count")
    if expected_count is not None and report.get("case_count") != expected_count:
        failures.append(f"黄金集数量应为 {expected_count}，实际为 {report.get('case_count')}")
    return {"passed": not failures, "failures": failures}
