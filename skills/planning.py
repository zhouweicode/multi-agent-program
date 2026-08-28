"""把 Skill 能力映射成现有 Agent 任务；调度仍由 Supervisor/LangGraph 负责。"""

from __future__ import annotations

from models.schemas import PlannedTask, SupervisorPlan
from tools.contracts import CapabilitySpec
from tools.registry import tool_registry

# 兼容已有导入名，能力定义的唯一来源是 ToolRegistry。
CapabilityBinding = CapabilitySpec
CAPABILITY_BINDINGS: dict[str, CapabilitySpec] = {
    item.name: item for item in tool_registry.list_capabilities()
}


def build_skill_plan(
    skill_id: str,
    capabilities: list[str],
    entity_ids: list[str],
    *,
    is_replan: bool = False,
    goal_context: str = "",
) -> SupervisorPlan:
    """同一能力只展开为一个领域任务；Skill 本身不执行这些任务。"""
    tasks: list[PlannedTask] = []
    seen_agents: set[str] = set()
    prefix = "replan" if is_replan else "skill"
    for capability in capabilities:
        binding = CAPABILITY_BINDINGS[capability]
        if binding.agent in seen_agents:
            continue
        seen_agents.add(binding.agent)
        tasks.append(
            PlannedTask(
                task_id=f"{prefix}_{skill_id}_{binding.domain}",
                agent=binding.agent,
                goal=(
                    f"{binding.goal}。用户请求与参数：{goal_context}"
                    if goal_context
                    else binding.goal
                ),
                required_fact_types=list(binding.required_fact_types),
                required_entity_ids=entity_ids,
            )
        )
    return SupervisorPlan(
        tasks=tasks,
        execution_mode="parallel",
        reason=f"Skill {skill_id} 请求 {len(tasks)} 组领域能力，由 Supervisor 展开后依赖感知调度",
    )
