"""Task-scoped retrieval plans and deterministic completion checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models.schemas import RetrievalPlan
from tools.registry import tool_registry


def build_retrieval_plan(
    agent_name: str, goal: str, required_fact_types: list[str],
    preferred_tools: list[str] | None = None,
    authorized_tool_names: list[str] | None = None,
) -> RetrievalPlan:
    required = list(dict.fromkeys(required_fact_types))
    tool_names = []
    mapping = tool_registry.fact_type_to_tool
    for fact_type in required:
        tool_name = mapping.get(fact_type)
        if tool_name and tool_name not in tool_names:
            tool_names.append(tool_name)
    if authorized_tool_names is not None:
        authorized = set(authorized_tool_names)
    else:
        authorized = set(tool_registry.tool_names(tool_registry.get_agent(agent_name).domain))
    preferred = [name for name in preferred_tools or [] if name in authorized]
    candidate_tools = list(dict.fromkeys([
        *preferred,
        *(name for name in tool_names if name in authorized),
    ]))
    return RetrievalPlan(
        goal=goal,
        required_fact_types=required,
        candidate_tools=candidate_tools,
        preferred_tools=preferred,
        stop_condition=(
            "所有 required_fact_types 均已有成功 Tool Observation"
            if required else "取得足以回答目标的可追溯证据"
        ),
    )


@dataclass(frozen=True)
class CompletionDecision:
    complete: bool
    missing_fact_types: tuple[str, ...] = ()


class RequiredFactsCompletionPolicy:
    def __init__(self, required_fact_types: list[str]):
        self.required_fact_types = tuple(dict.fromkeys(required_fact_types))

    def evaluate(self, observations: list[dict[str, Any]]) -> CompletionDecision:
        successful_tools = {
            str(item.get("tool")) for item in observations if item.get("success")
        }
        mapping = tool_registry.fact_type_to_tool
        missing = tuple(
            fact_type for fact_type in self.required_fact_types
            if mapping.get(fact_type) not in successful_tools
        )
        return CompletionDecision(not missing, missing)
