"""Router Node：结构化分类，不调用业务工具。"""
import logging
import re

from graph.state import GraphRAGState
from models.llm import ModelFactory
from services.observability import emit_event
from services.resources import get_entity_service

logger = logging.getLogger(__name__)

# 对具有明确业务含义的单领域关键词做确定性保护，避免模型输出格式正确但领域值错误。
DOMAIN_KEYWORDS = {
    "achievement": ("论文", "发表", "专利", "科研成果", "科研项目", "学术成果"),
    "talent": ("在哪里工作", "工作单位", "任职", "履历", "教育经历", "同事", "校友", "专家画像"),
    "enterprise": ("企业任职", "公司任职", "企业顾问", "企业项目", "企业专利", "技术合作"),
    "industry": ("产业链", "产业节点", "产业事件", "产业全景"),
    "graph": ("间接关系", "多跳", "路径", "一跳邻居", "局部子图", "关系强度"),
    "web": ("联网", "网络搜索", "外部来源", "公开资料", "官网", "新闻", "最新", "近期", "实时", "查证"),
}

_PERSON_NAME_RE = re.compile(r"^[\u4e00-\u9fff]{2,4}$")
_NON_PERSON_SUFFIXES = ("大学", "学院", "研究院", "研究所", "实验室", "公司", "集团", "产业", "协会", "中心")
_NON_PERSON_PREFIXES = ("分析", "查询", "对比", "验证", "判断", "综合", "联网")
_VERIFICATION_KEYWORDS = ("长期稳定", "核心科研合作伙伴", "稳定的产学研合作", "合作验证", "语义验证")


def _looks_like_person_name(value: str) -> bool:
    value = value.strip()
    return (bool(_PERSON_NAME_RE.fullmatch(value)) and not value.endswith(_NON_PERSON_SUFFIXES)
            and not value.startswith(_NON_PERSON_PREFIXES))


def _reconcile_person_mentions(question: str, model_mentions: list[str], discovered: list[str]) -> list[str]:
    """合并权威命中与可信模型结果，并补出“张伟和李明”中的并列姓名。

    权威库只能证明某个名字存在，不能据此断言问题中的其他名字不存在。
    """
    mentions = list(dict.fromkeys(discovered + [item.strip() for item in model_mentions
                                                if _looks_like_person_name(item)]))
    for seed in tuple(mentions):
        patterns = (
            rf"{re.escape(seed)}[和与、]([\u4e00-\u9fff]{{2,4}}?)(?=的|在|之间|共同|合作|关系|[，。？！,!?]|$)",
            rf"(?:^|[，。！？；,!?;\s])([\u4e00-\u9fff]{{2,4}}?)[和与、]{re.escape(seed)}(?=的|在|之间|共同|合作|关系|[，。？！,!?]|$)",
        )
        for pattern in patterns:
            for candidate in re.findall(pattern, question):
                if _looks_like_person_name(candidate) and candidate not in mentions:
                    mentions.append(candidate)
    return mentions


def _apply_domain_guardrail(question: str, output):
    """仅在命中唯一明确领域时校正主领域，不干预真正的跨领域判断。"""
    matched = [domain for domain, keywords in DOMAIN_KEYWORDS.items()
               if any(keyword in question for keyword in keywords)]
    if len(matched) == 1 and output.primary_domain != matched[0]:
        logger.warning("Router 领域校正: model=%s guardrail=%s question=%s",
                       output.primary_domain, matched[0], question)
        return output.model_copy(update={"primary_domain": matched[0]})
    return output


def _apply_web_search_policy(question: str, output, enabled: bool):
    """关闭联网时移除混合问题中的 Web 领域；纯联网问题保留以返回明确提示。"""
    if enabled:
        return output
    non_web = [domain for domain, keywords in DOMAIN_KEYWORDS.items()
               if domain != "web" and any(keyword in question for keyword in keywords)]
    if output.primary_domain == "web" and non_web:
        return output.model_copy(update={
            "primary_domain": non_web[0],
            "complexity": "complex" if len(non_web) > 1 else "simple",
        })
    if output.primary_domain != "web" and output.complexity == "complex" and len(non_web) <= 1:
        return output.model_copy(update={"complexity": "simple"})
    return output


def _apply_verification_guardrail(question: str, output):
    """Verification只处理明确的复杂关系判断，避免普通事实查询误入验证链路。"""
    required = any(keyword in question for keyword in _VERIFICATION_KEYWORDS)
    return output if output.requires_verification == required else output.model_copy(
        update={"requires_verification": required})


def router_node(state: GraphRAGState) -> dict:
    output = ModelFactory.structured_model().invoke_router(state["question"])
    output = _apply_domain_guardrail(state["question"], output)
    output = _apply_web_search_policy(state["question"], output, state.get("web_search_enabled", True))
    output = _apply_verification_guardrail(state["question"], output)
    # 权威库用于纠正机构误判，但不能静默删掉问题中尚未入库的人名。
    try:
        discovered = get_entity_service().mentions_in_text(state["question"])
        mentions = ([] if output.primary_domain == "industry" else
                    _reconcile_person_mentions(state["question"], output.entity_mentions, discovered))
        if discovered or output.primary_domain == "industry" or mentions != output.entity_mentions:
            output = output.model_copy(update={"entity_mentions": mentions})
    except Exception:
        logger.exception("Router 实体 mention 权威校正失败，保留模型结果")
    logger.info("Router: complexity=%s domain=%s mentions=%s", output.complexity, output.primary_domain, output.entity_mentions)
    emit_event("ROUTER_COMPLETED", thread_id=state.get("thread_id"), complexity=output.complexity, primary_domain=output.primary_domain,
               entity_mentions=output.entity_mentions)
    return output.model_dump()
