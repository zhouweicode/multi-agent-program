"""Supervisor/Planner Node：只拆解、调度与 replan，不调用业务 Tool。"""
import logging

from graph.state import GraphRAGState
from models.contracts import DEFAULT_REQUIRED_FACT_TYPES, required_fact_types
from models.llm import ModelFactory
from models.schemas import PlannedTask
from services.observability import emit_event
from services.telemetry import traced_span

logger = logging.getLogger(__name__)

DOMAIN_TASKS = {
    "talent_agent": "分析两位专家的共同任职经历、时间重叠和职业关系",
    "achievement_agent": "分析两位专家的共同论文、共同项目及科研合作时间",
    "enterprise_agent": "分析两位专家在企业中的角色、共同企业项目和共同企业专利",
    "industry_agent": "查询产业链结构、产业节点、相关企业和产业事件",
    "graph_reasoning_agent": "查询两位专家的多跳路径、间接关系和路径强度",
    "web_research_agent": "搜索公开网页来源，并提取带 URL 的外部候选证据",
}


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
    if any(word in question for word in ("间接关系", "多跳", "路径", "邻居", "关系强度", "局部子图")):
        required.append("graph_reasoning_agent")
    if web_search_enabled and any(word in question for word in ("联网", "网络搜索", "外部来源", "公开资料", "官网", "新闻", "最新", "近期", "实时", "查证")):
        required.append("web_research_agent")
    if not required:
        return plan
    tasks = [PlannedTask(task_id=f"task_{agent.removesuffix('_agent')}", agent=agent,
                         goal=DOMAIN_TASKS[agent],
                         required_fact_types=DEFAULT_REQUIRED_FACT_TYPES[agent],
                         required_entity_ids=[])
             for agent in dict.fromkeys(required)]
    return plan.model_copy(update={"tasks": tasks, "execution_mode": "parallel",
                                   "reason": "根据问题中的明确领域信号校正任务边界并并行执行"})


def supervisor_node(state: GraphRAGState) -> dict:
    if state.get("replan_count", 0) >= state.get("max_replans", 2):
        logger.warning("Supervisor: 已达到 max_replans")
        return {"tasks": [], "plan": {"reason": "达到最大重规划次数"}}
    replan_context = bool(state.get("validation_result") or state.get("verification_result"))
    with traced_span("supervisor.model.invoke", "model_operation", {
        "model.operation": "plan", "workflow.replan": replan_context,
    }):
        plan = ModelFactory.structured_model().invoke_supervisor(
            state["question"], state["resolved_entities"], state.get("validation_result"),
            state.get("verification_result"), state.get("task_history", []))
    is_replan = bool(state.get("validation_result") or state.get("verification_result"))
    plan = _guard_complex_plan(state["question"], plan, is_replan, state.get("web_search_enabled", True))
    entity_ids = list(state.get("resolved_entities", {}).values())
    normalized_tasks = []
    for task in plan.tasks:
        normalized_tasks.append(task.model_copy(update={
            "required_fact_types": required_fact_types(task.agent, state["question"]),
            "required_entity_ids": entity_ids,
        }))
    plan = plan.model_copy(update={"tasks": normalized_tasks})
    logger.info("Supervisor: mode=%s tasks=%s", plan.execution_mode, [x.agent for x in plan.tasks])
    emit_event("SUPERVISOR_PLANNED", thread_id=state.get("thread_id"), execution_mode=plan.execution_mode, agents=[x.agent for x in plan.tasks],
               replan_count=state.get("replan_count", 0), reason=plan.reason)
    return {"plan": plan.model_dump(), "tasks": [x.model_dump() for x in plan.tasks],
            "replan_count": state.get("replan_count", 0) + (1 if is_replan else 0)}
