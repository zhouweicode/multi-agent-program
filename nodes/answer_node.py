"""Answer Node：组合领域 Formatter，不调用模型、不自行补充事实。"""
import logging

from formatters.achievement_formatter import format_achievement
from formatters.enterprise_formatter import format_enterprise
from formatters.graph_formatter import format_graph
from formatters.industry_formatter import format_industry
from formatters.talent_formatter import format_talent
from formatters.web_formatter import format_web
from graph.state import GraphRAGState
from services.memory_manager import memory_manager
from services.observability import emit_event

logger = logging.getLogger(__name__)


def answer_node(state: GraphRAGState) -> dict:
    resolved = state.get("resolved_entities", {})
    entity_ids = set(resolved.values())
    names = "、".join(f"{name}（{entity_id}）" for name, entity_id in resolved.items())
    validation = state.get("validation_result", {})
    if not validation.get("valid"):
        answer = f"无法形成可靠结论。校验问题：{'；'.join(validation.get('errors', []) + validation.get('missing_domains', []))}"
        emit_event("ANSWER_GENERATED", thread_id=state.get("thread_id"), validation_status="FAIL", has_verification=False)
        return {"final_answer": answer}
    if state.get("requested_skill") in {"expert_report", "industry_landscape"} and state.get("report_markdown"):
        emit_event("ANSWER_GENERATED", thread_id=state.get("thread_id"), validation_status="PASS",
                   has_verification=False, skill_id=state["requested_skill"])
        return {"final_answer": state["report_markdown"]}

    formatted = [
        format_talent(state.get("talent_result"), resolved),
        format_achievement(state.get("achievement_result"), resolved),
        format_enterprise(state.get("enterprise_result"), resolved),
        format_industry(state.get("industry_result"), resolved),
        format_graph(state.get("graph_result"), resolved),
        format_web(state.get("web_result"), resolved),
    ]
    sections = [text for text, _ in formatted if text]
    signals = [text.split("：", 1)[0] for text, supported in formatted if text and supported]
    format_facts = [
        fact for fact in state.get("long_term_memory_facts", [])
        if fact.get("category") == "output_format"
    ]
    table_fact = next(
        (fact for fact in format_facts if "表格" in str(fact.get("content") or "")),
        None,
    )
    if table_fact:
        escaped_sections = [text.replace("|", "&#124;") for text in sections]
        rows = [
            f"| {index} | {text} |"
            for index, text in enumerate(escaped_sections, 1)
        ]
        numbered = "\n".join(["| 序号 | 分析结果 |", "| ---: | --- |", *rows])
        applied_memory_fact_ids = [str(table_fact["fact_id"])]
    else:
        numbered = "\n".join(
            f"{index}. {text}。" for index, text in enumerate(sections, 1)
        )
        applied_memory_fact_ids = []

    verification = state.get("verification_result")
    semantic = ""
    if verification:
        verdict = "是" if verification["status"] == "PASS" else "不是"
        semantic = f"\n语义验证结论：{verdict}长期稳定的核心科研合作伙伴（{verification['status']}，置信度 {verification['confidence']:.0%}）。{verification['reason']}。"
    has_web = bool(state.get("web_result"))
    if len(entity_ids) > 1:
        synthesis = (f"\n综合结论：现有证据明确支持两人在{'、'.join(signals)}方面存在合作。"
                     if signals else "\n综合结论：现有返回证据不足以确认两人存在明确合作。")
    elif entity_ids and has_web:
        synthesis = "\n综合结论：以上包含知识图谱事实与可追溯的联网候选证据，两者需保持来源边界。"
    elif entity_ids:
        synthesis = "\n综合结论：以上为当前实体在知识图谱中已返回并通过校验的事实。"
    else:
        synthesis = ""
    # 证据明细保留在 Shared State 与执行轨迹中供审计，最终答案只展示结论，
    # 避免大量内部 evidence_id 影响可读性。
    has_graph_data = any(state.get(field) for field in (
        "talent_result", "achievement_result", "enterprise_result", "industry_result", "graph_result"))
    basis = ("基于当前知识图谱与联网公开来源返回、并通过结构和溯源规则校验的数据"
             if has_web and has_graph_data else
             ("基于联网公开来源返回并通过结构与溯源规则校验的数据"
              if has_web else "基于当前知识图谱返回并通过规则校验的数据"))
    footer = ("联网内容仅作为带来源 URL 的外部候选证据，未自动写入知识图谱，也不会覆盖图谱事实。"
              if has_web else "以上结论未使用知识图谱之外的事实。")
    subject = f"{names} 的分析如下" if names else "分析如下"
    answer = f"{basis}，{subject}：\n{numbered}{synthesis}{semantic}\n{footer}"
    emit_event("ANSWER_GENERATED", thread_id=state.get("thread_id"), validation_status="PASS",
               has_verification=bool(verification),
               memory_fact_ids=applied_memory_fact_ids)
    if applied_memory_fact_ids and state.get("user_id"):
        try:
            memory_manager().mark_facts_applied(
                str(state["user_id"]), applied_memory_fact_ids
            )
        except Exception:  # usage accounting must not block a validated answer
            logger.exception("长期记忆应用次数写入失败")
    return {"final_answer": answer,
            "long_term_memory_applied_fact_ids": applied_memory_fact_ids}
