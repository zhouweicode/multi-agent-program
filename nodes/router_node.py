"""Router Node：结构化分类，不调用业务工具。"""
import logging
from graph.state import GraphRAGState
from models.llm import ModelFactory
from services.observability import emit_event

logger = logging.getLogger(__name__)

# 对具有明确业务含义的单领域关键词做确定性保护，避免模型输出格式正确但领域值错误。
DOMAIN_KEYWORDS = {
    "achievement": ("论文", "发表", "专利", "科研成果", "科研项目", "学术成果"),
    "talent": ("在哪里工作", "工作单位", "任职", "履历", "教育经历", "同事", "校友", "专家画像"),
    "enterprise": ("企业任职", "公司任职", "企业顾问", "企业项目", "企业专利", "技术合作"),
    "industry": ("产业链", "产业节点", "产业事件", "产业全景"),
    "graph": ("间接关系", "多跳", "路径", "一跳邻居", "局部子图", "关系强度"),
}


def _apply_domain_guardrail(question: str, output):
    """仅在命中唯一明确领域时校正主领域，不干预真正的跨领域判断。"""
    matched = [domain for domain, keywords in DOMAIN_KEYWORDS.items()
               if any(keyword in question for keyword in keywords)]
    if len(matched) == 1 and output.primary_domain != matched[0]:
        logger.warning("Router 领域校正: model=%s guardrail=%s question=%s",
                       output.primary_domain, matched[0], question)
        return output.model_copy(update={"primary_domain": matched[0]})
    return output


def router_node(state: GraphRAGState) -> dict:
    output = ModelFactory.structured_model().invoke_router(state["question"])
    output = _apply_domain_guardrail(state["question"], output)
    logger.info("Router: complexity=%s domain=%s mentions=%s", output.complexity, output.primary_domain, output.entity_mentions)
    emit_event("ROUTER_COMPLETED", thread_id=state.get("thread_id"), complexity=output.complexity, primary_domain=output.primary_domain,
               entity_mentions=output.entity_mentions)
    return output.model_dump()
