"""Router Node：结构化分类，不调用业务工具。"""
import logging
from graph.state import GraphRAGState
from models.llm import ModelFactory

logger = logging.getLogger(__name__)


def router_node(state: GraphRAGState) -> dict:
    output = ModelFactory.structured_model().invoke_router(state["question"])
    logger.info("Router: complexity=%s domain=%s mentions=%s", output.complexity, output.primary_domain, output.entity_mentions)
    return output.model_dump()

