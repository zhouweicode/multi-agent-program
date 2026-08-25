"""Answer Node：组合领域 Formatter，不调用模型、不补充外部知识。"""
from formatters.achievement_formatter import format_achievement
from formatters.enterprise_formatter import format_enterprise
from formatters.graph_formatter import format_graph
from formatters.industry_formatter import format_industry
from formatters.talent_formatter import format_talent
from graph.state import GraphRAGState
from services.observability import emit_event


def answer_node(state: GraphRAGState) -> dict:
    resolved = state.get("resolved_entities", {})
    entity_ids = set(resolved.values())
    names = "、".join(f"{name}（{entity_id}）" for name, entity_id in resolved.items())
    validation = state.get("validation_result", {})
    if not validation.get("valid"):
        answer = f"无法形成可靠结论。校验问题：{'；'.join(validation.get('errors', []) + validation.get('missing_domains', []))}"
        emit_event("ANSWER_GENERATED", thread_id=state.get("thread_id"), validation_status="FAIL", has_verification=False)
        return {"final_answer": answer}

    formatted = [
        format_talent(state.get("talent_result"), resolved),
        format_achievement(state.get("achievement_result"), resolved),
        format_enterprise(state.get("enterprise_result"), resolved),
        format_industry(state.get("industry_result"), resolved),
        format_graph(state.get("graph_result"), resolved),
    ]
    sections = [text for text, _ in formatted if text]
    signals = [text.split("：", 1)[0] for text, supported in formatted if text and supported]
    numbered = "\n".join(f"{index}. {text}。" for index, text in enumerate(sections, 1))

    verification = state.get("verification_result")
    semantic = ""
    if verification:
        verdict = "是" if verification["status"] == "PASS" else "不是"
        semantic = f"\n语义验证结论：{verdict}长期稳定的核心科研合作伙伴（{verification['status']}，置信度 {verification['confidence']:.0%}）。{verification['reason']}。"
    if len(entity_ids) > 1:
        synthesis = (f"\n综合结论：现有证据明确支持两人在{'、'.join(signals)}方面存在合作。"
                     if signals else "\n综合结论：现有返回证据不足以确认两人存在明确合作。")
    else:
        synthesis = "\n综合结论：以上为当前实体在知识图谱中已返回并通过校验的事实。"
    # 证据明细保留在 Shared State 与执行轨迹中供审计，最终答案只展示结论，
    # 避免大量内部 evidence_id 影响可读性。
    answer = f"基于当前知识图谱返回并通过规则校验的数据，{names or '当前查询实体'} 的分析如下：\n{numbered}{synthesis}{semantic}\n以上结论未使用知识图谱之外的事实。"
    emit_event("ANSWER_GENERATED", thread_id=state.get("thread_id"), validation_status="PASS", has_verification=bool(verification))
    return {"final_answer": answer}
