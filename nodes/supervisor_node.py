"""Supervisor/Planner Node：只拆解、调度与 replan，不调用业务 Tool。"""
import logging
from graph.state import GraphRAGState
from models.llm import ModelFactory
from services.observability import emit_event

logger = logging.getLogger(__name__)


def supervisor_node(state: GraphRAGState) -> dict:
    if state.get("replan_count", 0) >= state.get("max_replans", 2):
        logger.warning("Supervisor: 已达到 max_replans")
        return {"tasks": [], "plan": {"reason": "达到最大重规划次数"}}
    plan = ModelFactory.structured_model().invoke_supervisor(
        state["question"], state["resolved_entities"], state.get("validation_result"),
        state.get("verification_result"), state.get("task_history", []))
    logger.info("Supervisor: mode=%s tasks=%s", plan.execution_mode, [x.agent for x in plan.tasks])
    is_replan = bool(state.get("validation_result") or state.get("verification_result"))
    emit_event("SUPERVISOR_PLANNED", thread_id=state.get("thread_id"), execution_mode=plan.execution_mode, agents=[x.agent for x in plan.tasks],
               replan_count=state.get("replan_count", 0), reason=plan.reason)
    return {"plan": plan.model_dump(), "tasks": [x.model_dump() for x in plan.tasks],
            "replan_count": state.get("replan_count", 0) + (1 if is_replan else 0)}
