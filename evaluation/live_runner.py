"""Small, repeated real-model evaluation with stability and runtime metrics."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from statistics import mean
from typing import Any

from evaluation.runner import _percentile, _ratio, evaluate_case, load_cases
from models.settings import Settings


def _signature(row: dict[str, Any]) -> str:
    if row.get("signature_override"):
        return str(row["signature_override"])
    if row["case_type"] == "routing":
        actual = row.get("actual", {})
        payload = {
            key: actual.get(key)
            for key in (
                "primary_domain", "complexity", "requires_verification",
                "verification_claim_type", "entity_mentions",
            )
        }
    else:
        payload = {
            "agents": row.get("actual_agents", []),
            "tools": row.get("actual_tools", []),
            "validation": row.get("validation", {}).get("valid"),
            "stop_reasons": row.get("agent_stop_reasons", []),
        }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _tool_plan_signature(row: dict[str, Any]) -> str:
    if row.get("signature_override"):
        return str(row["signature_override"])
    return json.dumps({
        "agents": row.get("actual_agents", []),
        "tools": row.get("actual_tools", []),
    }, ensure_ascii=False, sort_keys=True)


def _group_consistency(
    rows: list[dict[str, Any]],
    signature: Callable[[dict[str, Any]], str] = _signature,
) -> float | None:
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        grouped[row["case_id"]].append(signature(row))
    if not grouped:
        return None
    scores = [
        (
            0.0 if any(value.startswith("__ERROR__:") for value in signatures)
            else max(Counter(signatures).values()) / len(signatures)
        )
        for signatures in grouped.values()
    ]
    return round(mean(scores), 4)


def summarize_live_runs(
    rows: list[dict[str, Any]], *, provider: str, model_name: str, repeats: int,
) -> dict[str, Any]:
    workflows = [row for row in rows if row["case_type"] == "workflow"]
    routings = [row for row in rows if row["case_type"] == "routing"]
    agent_runs = sum(row.get("agent_run_count", 0) for row in workflows)
    metrics = {
        "case_pass_rate": _ratio(sum(row["passed"] for row in rows), len(rows)),
        "routing_consistency": _group_consistency(routings),
        "workflow_consistency": _group_consistency(workflows),
        "tool_plan_consistency": _group_consistency(
            workflows, _tool_plan_signature
        ),
        "p95_latency_ms": _percentile(
            [row["duration_ms"] for row in workflows], 0.95
        ),
        "average_latency_ms": (
            round(mean(row["duration_ms"] for row in workflows), 3)
            if workflows else None
        ),
        "invalid_tool_call_rate": _ratio(
            sum(row.get("invalid_tool_call_count", 0) for row in workflows),
            agent_runs,
        ),
        "incomplete_agent_rate": _ratio(
            sum(row.get("incomplete_agent_count", 0) for row in workflows),
            agent_runs,
        ),
        "completion_failure_rate": _ratio(
            sum(row.get("incomplete_agent_count", 0) for row in workflows),
            agent_runs,
        ),
        "no_progress_stop_rate": _ratio(
            sum(
                reason == "AGENT_NO_PROGRESS"
                for row in workflows
                for reason in row.get("agent_stop_reasons", [])
            ),
            agent_runs,
        ),
        "average_replans": (
            round(mean(row.get("replan_count", 0) for row in workflows), 4)
            if workflows else None
        ),
        "average_model_tokens": (
            round(mean(row.get("agent_total_tokens", 0) for row in workflows), 3)
            if workflows else None
        ),
        "average_model_cost": (
            round(mean(row.get("agent_total_cost", 0) for row in workflows), 8)
            if workflows else None
        ),
    }
    return {
        "mode": "live",
        "provider": provider,
        "model_name": model_name,
        "repeats": repeats,
        "case_count": len({row["case_id"] for row in rows}),
        "run_count": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "metrics": metrics,
        "cases": rows,
    }


def evaluate_live_dataset(
    path: str | Path,
    *,
    case_types: tuple[str, ...] = ("routing", "workflow"),
    limit: int = 10,
    repeats: int = 2,
    allow_mock: bool = False,
    evaluator: Callable[[dict[str, Any]], dict[str, Any]] = evaluate_case,
) -> dict[str, Any]:
    settings = Settings.from_env()
    if settings.model_provider == "mock" and not allow_mock:
        raise ValueError(
            "真实模型评测拒绝 MODEL_PROVIDER=mock；请配置模型凭据，"
            "或仅在测试评测器时显式使用 allow_mock=True"
        )
    selected = [
        case for case in load_cases(path) if case.get("case_type") in case_types
    ][:max(1, limit)]
    if not selected:
        raise ValueError("没有匹配 case_types 的评测用例")
    repeat_count = max(1, repeats)
    rows = []
    for repeat in range(1, repeat_count + 1):
        for case in selected:
            try:
                row = evaluator(case)
            except Exception as exc:  # noqa: BLE001 - one failed case must not discard the report.
                row = {
                    "case_id": case["case_id"],
                    "case_type": case["case_type"],
                    "passed": False,
                    "evaluation_error": f"{type(exc).__name__}: {exc}",
                }
                row["signature_override"] = f"__ERROR__:{row['evaluation_error']}"
            rows.append(row | {"repeat": repeat})
    return summarize_live_runs(
        rows,
        provider=settings.model_provider,
        model_name=settings.model_name,
        repeats=repeat_count,
    )
