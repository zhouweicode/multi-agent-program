"""Supervisor/Planner Node：只拆解、调度与 replan，不调用业务 Tool。"""
import hashlib
import logging

from graph.state import GraphRAGState
from models.contracts import DEFAULT_REQUIRED_FACT_TYPES, required_fact_types
from models.llm import ModelFactory
from models.schemas import PlannedTask
from models.settings import Settings
from services.observability import emit_event
from services.telemetry import traced_span
from skills.expert_report.spec import (
    selected_capabilities as expert_report_capabilities,
)
from skills.industry_landscape.spec import (
    selected_capabilities as industry_landscape_capabilities,
)
from skills.planning import CAPABILITY_BINDINGS, build_skill_plan
from skills.registry import skill_registry
from tools.registry import tool_registry

logger = logging.getLogger(__name__)

DOMAIN_TASKS = {
    "talent_agent": "分析两位专家的共同任职经历、时间重叠和职业关系",
    "achievement_agent": "分析两位专家的共同论文、共同项目及科研合作时间",
    "enterprise_agent": "分析两位专家在企业中的角色、共同企业项目和共同企业专利",
    "industry_agent": "查询产业链结构、产业节点、相关企业和产业事件",
    "graph_reasoning_agent": "查询两位专家的多跳路径、间接关系和路径强度",
    "web_research_agent": "搜索公开网页来源，并提取带 URL 的外部候选证据",
}


def _apply_experience_advice(plan, state: GraphRAGState, is_replan: bool):
    """Apply only gated strategy hints; never bypass Router or Validator."""
    mode = Settings.from_env().query_experience_mode
    match = state.get("experience_match") or {}
    strategy = state.get("experience_strategy") or {}
    if mode == "shadow" or is_replan or not match.get("applicable") or not strategy:
        return plan
    tools_by_agent = strategy.get("tools_by_agent") or {}
    updated = []
    for task in plan.tasks:
        domain = tool_registry.get_agent(task.agent).domain
        allowed = set(tool_registry.tool_names(domain))
        preferred = [name for name in tools_by_agent.get(task.agent, []) if name in allowed]
        updated.append(task.model_copy(update={"preferred_tools": preferred}))
    if mode == "active" and state.get("experience_route_agreement"):
        order = {agent: index for index, agent in enumerate(strategy.get("agents") or [])}
        # Active mode may reorder the same safe plan, but cannot add or remove domains.
        if set(order) == {task.agent for task in updated}:
            updated.sort(key=lambda task: order[task.agent])
    emit_event(
        "EXPERIENCE_STRATEGY_ADVISED" if mode == "advisory" else "EXPERIENCE_STRATEGY_APPLIED",
        thread_id=state.get("thread_id"), mode=mode,
        pattern_id=match.get("pattern_id"),
        agents=[task.agent for task in updated],
        preferred_tools={task.agent: task.preferred_tools for task in updated},
    )
    reason = plan.reason + (
        "；已附加历史策略建议" if mode == "advisory"
        else "；已应用通过门禁的历史任务顺序和工具偏好"
    )
    return plan.model_copy(update={"tasks": updated, "reason": reason})


def _guard_complex_plan(question: str, plan, is_replan: bool, web_search_enabled: bool = True):
    """对初次复杂规划做领域边界保护；重规划仍以 Validator 缺失项为准。"""
    if is_replan:
        return plan
    required = []
    if any(word in question for word in ("职业", "任职", "同事", "校友")):
        required.append("talent_agent")
    if any(word in question for word in ("学术", "论文", "科研", "专利", "项目")):
        required.append("achievement_agent")
    if any(word in question for word in ("企业", "公司", "产业合作", "技术合作", "顾问")):
        required.append("enterprise_agent")
    if any(word in question for word in ("产业链", "产业节点", "产业事件", "产业全景")):
        required.append("industry_agent")
    if any(word in question for word in (
        "间接关系", "多跳", "路径", "邻居", "关系强度", "局部子图",
        "图统计", "图聚合", "图 Schema", "图Schema", "图模式", "图谱结构",
    )):
        required.append("graph_reasoning_agent")
    if web_search_enabled and any(word in question for word in ("联网", "网络搜索", "外部来源", "公开资料", "官网", "新闻", "最新", "近期", "实时", "查证")):
        required.append("web_research_agent")
    if not required:
        return plan
    tasks = []
    for agent in dict.fromkeys(required):
        planned = [task for task in plan.tasks if task.agent == agent]
        tasks.extend(planned or [PlannedTask(
            task_id=f"task_{agent.removesuffix('_agent')}", agent=agent,
            goal=DOMAIN_TASKS[agent],
            required_fact_types=DEFAULT_REQUIRED_FACT_TYPES[agent],
            required_entity_ids=[],
        )])
    return plan.model_copy(update={"tasks": tasks,
                                   "reason": "根据问题中的明确领域信号校正任务边界并执行"})


def supervisor_node(state: GraphRAGState) -> dict:
    if state.get("replan_count", 0) >= state.get("max_replans", 2):
        logger.warning("Supervisor: 已达到 max_replans")
        return {"tasks": [], "plan": {"reason": "达到最大重规划次数"}}
    is_replan = bool(state.get("validation_result") or state.get("verification_result"))
    entity_ids = list(state.get("resolved_entities", {}).values())
    skill_id = state.get("requested_skill")
    skill_update = {}
    if skill_id:
        spec = skill_registry.get(skill_id)
        instructions = spec.load_instructions()
        capability_selector = (industry_landscape_capabilities if skill_id == "industry_landscape"
                               else expert_report_capabilities)
        capabilities = capability_selector(state.get("skill_input", {}), state.get("web_search_enabled", True))
        plan = build_skill_plan(
            skill_id, capabilities, entity_ids, is_replan=is_replan,
            goal_context=f"{state['question']}；配置={state.get('skill_input', {})}",
        )
        required_domains = list(dict.fromkeys(
            CAPABILITY_BINDINGS[item].domain for item in spec.required_capabilities
        ))
        skill_update = {
            "skill_capabilities": capabilities,
            "skill_required_domains": required_domains,
            "skill_instruction_digest": hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
        }
        emit_event("SKILL_PLAN_CREATED", thread_id=state.get("thread_id"), skill_id=skill_id,
                   capabilities=capabilities, required_domains=required_domains)
    else:
        with traced_span("supervisor.model.invoke", "model_operation", {
            "model.operation": "plan", "workflow.replan": is_replan,
            "memory.fact_count": len(state.get("long_term_memory_used_fact_ids", [])),
        }):
            model = ModelFactory.structured_model()
            arguments = (
                state["question"], state["resolved_entities"],
                state.get("validation_result"), state.get("verification_result"),
                state.get("task_history", []),
            )
            memory_context = state.get("long_term_memory_prompt")
            plan = (model.invoke_supervisor(*arguments, memory_context=memory_context)
                    if memory_context else model.invoke_supervisor(*arguments))
        plan = _guard_complex_plan(state["question"], plan, is_replan, state.get("web_search_enabled", True))
        plan = _apply_experience_advice(plan, state, is_replan)
    normalized_tasks = []
    for task in plan.tasks:
        normalized_tasks.append(task.model_copy(update={
            "required_fact_types": (
                task.required_fact_types
                or required_fact_types(task.agent, state["question"])
            ),
            "required_entity_ids": entity_ids,
        }))
    task_ids = [task.task_id for task in normalized_tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("Supervisor 计划包含重复 task_id")
    plan = plan.model_copy(update={"tasks": normalized_tasks})
    logger.info("Supervisor: mode=%s tasks=%s", plan.execution_mode, [x.agent for x in plan.tasks])
    emit_event("SUPERVISOR_PLANNED", thread_id=state.get("thread_id"), execution_mode=plan.execution_mode, agents=[x.agent for x in plan.tasks],
               replan_count=state.get("replan_count", 0), reason=plan.reason)
    return {"plan": plan.model_dump(), "tasks": [x.model_dump() for x in plan.tasks],
            "replan_count": state.get("replan_count", 0) + (1 if is_replan else 0)} | skill_update
