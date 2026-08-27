"""把 Skill 能力映射成现有 Agent 任务；调度仍由 Supervisor/LangGraph 负责。"""
from __future__ import annotations

from dataclasses import dataclass

from models.schemas import PlannedTask, SupervisorPlan


@dataclass(frozen=True)
class CapabilityBinding:
    agent: str
    goal: str
    required_fact_types: tuple[str, ...]
    domain: str


CAPABILITY_BINDINGS: dict[str, CapabilityBinding] = {
    "expert_profile_history": CapabilityBinding(
        agent="talent_agent",
        goal="为专家报告查询单个专家的基础画像、教育经历和任职经历",
        required_fact_types=("person_profile", "education", "employment"),
        domain="talent",
    ),
    "research_achievements": CapabilityBinding(
        agent="achievement_agent",
        goal="为专家报告查询单个专家的论文和专利成果",
        required_fact_types=("papers", "patents"),
        domain="achievement",
    ),
    "enterprise_relations": CapabilityBinding(
        agent="enterprise_agent",
        goal="为专家报告查询专家的企业角色，以及与其相关的企业项目和企业专利",
        required_fact_types=("company_roles", "company_projects", "company_patents"),
        domain="enterprise",
    ),
    "cooperation_network": CapabilityBinding(
        agent="graph_reasoning_agent",
        goal="为专家报告查询专家的一跳合作与关联网络，并进行有限的局部子图扩展",
        required_fact_types=("neighbors",),
        domain="graph",
    ),
    "external_public_evidence": CapabilityBinding(
        agent="web_research_agent",
        goal="为专家报告搜索与目标专家直接相关的公开网页候选证据",
        required_fact_types=("web_sources",),
        domain="web",
    ),
    "industry_landscape_core": CapabilityBinding(
        agent="industry_agent",
        goal="为产业全景报告检索产业节点，并查询产业链结构、关联企业和重点产业事件",
        required_fact_types=("industry_segments", "chain_structure", "node_companies", "ranked_events"),
        domain="industry",
    ),
    "external_industry_evidence": CapabilityBinding(
        agent="web_research_agent",
        goal="为产业全景报告搜索与目标产业直接相关的公开网页候选证据",
        required_fact_types=("web_sources",),
        domain="web",
    ),
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
        tasks.append(PlannedTask(
            task_id=f"{prefix}_{skill_id}_{binding.domain}",
            agent=binding.agent,
            goal=(f"{binding.goal}。用户请求与参数：{goal_context}" if goal_context else binding.goal),
            required_fact_types=list(binding.required_fact_types),
            required_entity_ids=entity_ids,
        ))
    return SupervisorPlan(
        tasks=tasks,
        execution_mode="parallel",
        reason=f"Skill {skill_id} 请求 {len(tasks)} 组领域能力，由 Supervisor 展开后依赖感知调度",
    )
