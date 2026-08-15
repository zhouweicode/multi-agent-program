"""Answer Node：把已校验的结构化事实转成中文业务结论，不补充外部知识。"""
from graph.state import GraphRAGState
from services.observability import emit_event


def _facts(result: dict | None, tool_name: str) -> list:
    """收集同一工具多轮调用的结果，并保持原始顺序。"""
    values = []
    for fact in (result or {}).get("facts", []):
        if fact.get("tool") == tool_name:
            data = fact.get("data")
            values.extend(data if isinstance(data, list) else [data])
    return values


def _unique(rows: list[dict], key: str) -> list[dict]:
    seen, result = set(), []
    for row in rows:
        value = row.get(key)
        if value not in seen:
            seen.add(value)
            result.append(row)
    return result


def answer_node(state: GraphRAGState) -> dict:
    resolved = state.get("resolved_entities", {})
    entity_ids = set(resolved.values())
    names = "、".join(f"{name}（{eid}）" for name, eid in resolved.items())
    validation = state.get("validation_result", {})
    if not validation.get("valid"):
        answer = f"无法形成可靠结论。校验问题：{'；'.join(validation.get('errors', []) + validation.get('missing_domains', []))}"
        emit_event("ANSWER_GENERATED", thread_id=state.get("thread_id"), validation_status="FAIL", has_verification=False)
        return {"final_answer": answer}

    sections, signals = [], []

    # 人才机构领域：多人查询优先回答共同任职，单人查询回答个人履历。
    talent = state.get("talent_result")
    if talent:
        overlaps = _facts(talent, "match_employment_overlap")
        employments = _facts(talent, "get_employment_history")
        profiles = _facts(talent, "get_person_profile")
        if overlaps:
            detail = "；".join(f"{row['organization']}（自 {row['from_year']} 年起重叠）" for row in overlaps)
            sections.append(f"职业合作：两人存在共同任职经历，{detail}，因此具备同机构同事关系")
            signals.append("职业合作")
        elif len(entity_ids) > 1 and employments:
            shared = []
            for organization in dict.fromkeys(row["organization"] for row in employments):
                rows = [row for row in employments if row["organization"] == organization]
                covered = {row.get("entity_id") for row in rows}
                if entity_ids.issubset(covered):
                    shared.append(f"{organization}（自 {max(row['start_year'] for row in rows)} 年起重叠）")
            if shared:
                sections.append("职业合作：两人存在共同任职经历，" + "；".join(shared) + "，因此具备同机构同事关系")
                signals.append("职业合作")
            else:
                sections.append("职业合作：当前返回的任职证据中未发现两人在同一机构的时间重叠")
        elif employments:
            rows = []
            for item in employments:
                period = f"{item['start_year']} 年至今" if item.get("end_year") is None else f"{item['start_year']}—{item['end_year']} 年"
                rows.append(f"{item['organization']}，担任{item['role']}（{period}）")
            sections.append("任职经历：" + "；".join(rows))
        elif profiles:
            profile = profiles[0]
            sections.append(f"专家画像：{profile.get('organization', '机构未知')}，{profile.get('title', '职务未知')}")
        else:
            sections.append("职业合作：当前数据源未返回可用于判断共同任职的证据")

    # 科研成果领域：多人问题只把确实包含全部实体的论文称为共同论文。
    achievement = state.get("achievement_result")
    if achievement:
        common_papers = _facts(achievement, "get_common_papers")
        author_papers = _facts(achievement, "get_author_papers")
        if len(entity_ids) > 1:
            inferred_common = [row for row in author_papers if entity_ids.issubset(set(row.get("authors", [])))]
            papers = _unique(common_papers + inferred_common, "paper_id")
        else:
            papers = _unique(author_papers or common_papers, "paper_id")
        projects = _unique(_facts(achievement, "get_common_projects"), "project_id")
        parts = []
        if papers:
            label = "共同论文" if len(entity_ids) > 1 else "发表论文"
            parts.append(label + "：" + "；".join(f"《{row['title']}》（{row['year']}）" for row in papers))
        if projects:
            parts.append("共同项目：" + "；".join(
                f"{row['name']}（{row['start_year']}—{row['end_year']} 年）" for row in projects))
        if parts:
            sections.append("学术合作：" + "；".join(parts) if len(entity_ids) > 1 else parts[0])
            if len(entity_ids) > 1:
                signals.append("学术合作")
        else:
            sections.append("学术合作：当前数据源未返回共同论文或共同项目证据")

    # 企业领域：直接解释角色、共同项目和共同专利，不再输出 Agent 调试统计。
    enterprise = state.get("enterprise_result")
    if enterprise:
        roles = _facts(enterprise, "get_person_company_roles")
        projects = [row for row in _facts(enterprise, "get_company_projects")
                    if not entity_ids or entity_ids.issubset(set(row.get("participant_ids", [])))]
        patents = [row for row in _facts(enterprise, "get_company_patents")
                   if not entity_ids or entity_ids.issubset(set(row.get("inventor_ids", [])))]
        role_parts = []
        role_names = {entity_id: name for name, entity_id in resolved.items()}
        for row in roles:
            role_parts.append(f"{role_names.get(row['entity_id'], row['entity_id'])}在{row.get('company_name', row['company_id'])}担任{row['role']}（自 {row['start_year']} 年）")
        parts = []
        if role_parts:
            parts.append("企业角色：" + "；".join(role_parts))
        if projects:
            project_label = "共同企业项目" if len(entity_ids) > 1 else "相关企业项目"
            parts.append(project_label + "：" + "；".join(f"{row['name']}（{row.get('company_name', row['company_id'])}）" for row in projects))
        if patents:
            patent_label = "共同企业专利" if len(entity_ids) > 1 else "相关企业专利"
            parts.append(patent_label + "：" + "；".join(f"《{row['title']}》（{row.get('company_name', row['company_id'])}）" for row in patents))
        if parts:
            enterprise_title = "企业与产业合作" if len(entity_ids) > 1 else "企业关系"
            sections.append(enterprise_title + "：" + "；".join(parts))
            if len(entity_ids) > 1 and (projects or patents):
                signals.append("企业与产业合作")
        else:
            sections.append("企业与产业合作：当前数据源未发现两人的共同企业项目或共同企业专利")

    industry = state.get("industry_result")
    if industry:
        chains = _facts(industry, "get_chain_structure")
        companies = _facts(industry, "get_node_companies")
        events = _facts(industry, "rank_top_events") or _facts(industry, "get_node_events")
        parts = []
        if chains:
            parts.append("产业链：" + "；".join(row.get("name", row.get("chain_id", "未知产业链")) for row in chains))
        if companies:
            parts.append("相关企业：" + "、".join(row["name"] for row in companies))
        if events:
            parts.append("重点事件：" + "；".join(row["title"] for row in events))
        sections.append("产业链信息：" + "；".join(parts) if parts else "产业链信息：当前数据源未返回相关事实")

    graph_result = state.get("graph_result")
    if graph_result:
        paths = _facts(graph_result, "find_path")
        strengths = _facts(graph_result, "calculate_path_strength")
        parts = []
        for path in paths:
            if path.get("found"):
                parts.append(f"发现 {path.get('hop_count')} 跳关系路径：{' → '.join(path.get('nodes', []))}")
        for strength in strengths:
            if strength.get("found"):
                parts.append(f"路径关系强度为 {strength.get('strength')}")
        sections.append("图关系推理：" + "；".join(parts) if parts else "图关系推理：当前数据源未发现可用关系路径")

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
    answer = f"基于当前知识图谱返回并通过规则校验的数据，{names or '当前查询实体'} 的分析如下：\n{numbered}{synthesis}{semantic}\n以上结论未使用知识图谱之外的事实。"
    emit_event("ANSWER_GENERATED", thread_id=state.get("thread_id"), validation_status="PASS", has_verification=bool(verification))
    return {"final_answer": answer}
