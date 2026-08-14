"""Answer Node：仅基于图谱返回结果生成中文答案，不补充外部事实。"""
from graph.state import GraphRAGState


def answer_node(state: GraphRAGState) -> dict:
    names = "、".join(f"{name}（{eid}）" for name, eid in state.get("resolved_entities", {}).items())
    validation = state.get("validation_result", {})
    if not validation.get("valid"):
        return {"final_answer": f"无法形成可靠结论。校验问题：{'；'.join(validation.get('errors', []) + validation.get('missing_domains', []))}"}
    overlaps = []
    for fact in state.get("talent_result", {}).get("facts", []):
        if fact["tool"] == "match_employment_overlap":
            overlaps = fact["data"]
    papers = []
    for fact in state.get("achievement_result", {}).get("facts", []):
        if fact["tool"] == "get_common_papers":
            papers = fact["data"]
    verification = state.get("verification_result")
    sections = []
    if state.get("talent_result"):
        sections.append("职业关系：" + ("存在共同任职经历：" + "；".join(f"{x['organization']}（自 {x['from_year']} 年起重叠）" for x in overlaps) if overlaps else "Mock 数据中未发现共同任职经历"))
    if state.get("achievement_result"):
        sections.append("学术关系：" + ("共同论文：" + "；".join(f"《{x['title']}》（{x['year']}）" for x in papers) if papers else "Mock 数据中未发现共同论文"))
    for field, title in (("enterprise_result", "企业关系"), ("industry_result", "产业链"), ("graph_result", "图关系推理")):
        result = state.get(field)
        if result:
            fact_count = sum(len(f["data"]) if isinstance(f["data"], list) else 1 for f in result.get("facts", []))
            sections.append(f"{title}：{result['summary']}，返回 {fact_count} 条/组结构化事实")
    numbered = "\n".join(f"{index}. {text}。" for index, text in enumerate(sections, 1))
    subject = names or "当前查询实体"
    if verification:
        verdict = "是" if verification["status"] == "PASS" else "不是"
        semantic = f"\n语义验证结论：{verdict}长期稳定的核心科研合作伙伴（{verification['status']}，置信度 {verification['confidence']:.0%}）。{verification['reason']}。"
    else:
        semantic = ""
    return {"final_answer": f"基于当前知识图谱 Mock 数据，{subject} 的分析如下：\n{numbered}{semantic}\n以上结论仅来自已返回且通过规则校验的证据。"}
