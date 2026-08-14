"""Router Node：结构化分类，不调用业务工具。"""
import logging
from graph.state import GraphRAGState
from models.llm import ModelFactory
from services.observability import emit_event

logger = logging.getLogger(__name__)


def router_node(state: GraphRAGState) -> dict:
    output = ModelFactory.structured_model().invoke_router(state["question"])
    logger.info("Router: complexity=%s domain=%s mentions=%s", output.complexity, output.primary_domain, output.entity_mentions)
    emit_event("ROUTER_COMPLETED", thread_id=state.get("thread_id"), complexity=output.complexity, primary_domain=output.primary_domain,
               entity_mentions=output.entity_mentions)
    return output.model_dump()
