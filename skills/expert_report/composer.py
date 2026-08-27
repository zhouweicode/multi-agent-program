"""只基于已验证 State 组装专家报告，不调用模型、Agent 或 Tool。"""
from __future__ import annotations

from typing import Any, Iterable

from skills.expert_report.schemas import ExpertReport, ReportClaim, ReportSection
from skills.expert_report.spec import EXPERT_REPORT_SPEC


def _facts(result: dict[str, Any] | None, tool_name: str) -> list[Any]:
    return [fact.get("data") for fact in (result or {}).get("facts", [])
            if fact.get("tool") == tool_name]


def _rows(result: dict[str, Any] | None, tool_name: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for data in _facts(result, tool_name):
        if isinstance(data, list):
            values.extend(item for item in data if isinstance(item, dict))
        elif isinstance(data, dict):
            values.append(data)
    return values


def _row_evidence_ids(row: dict[str, Any]) -> list[str]:
    values = row.get("evidence_ids") or ([row.get("evidence_id")] if row.get("evidence_id") else [])
    return [str(item) for item in values if item]


def _claim(text: str, rows: Iterable[dict[str, Any]], evidence_map: dict[str, dict[str, Any]],
           confidence: float = 1.0) -> ReportClaim | None:
    ids = list(dict.fromkeys(evidence_id for row in rows for evidence_id in _row_evidence_ids(row)
                             if evidence_id in evidence_map))
    return ReportClaim(text=text, evidence_ids=ids, confidence=confidence) if ids else None


def _section(section_id: str, title: str, claims: list[ReportClaim | None], *, requested: bool = True,
             errors: list[str] | None = None) -> ReportSection | None:
    if not requested:
        return None
    supported = [item for item in claims if item is not None]
    if errors:
        status = "partial" if supported else "unavailable"
        summary = f"本章节存在 {len(errors)} 项执行异常，仅展示已取得且可追溯的事实。"
    elif supported:
        status = "complete"
        summary = f"本章节包含 {len(supported)} 条有证据支持的陈述。"
    else:
        status = "unavailable"
        summary = "当前数据源未返回可形成可追溯陈述的记录。"
    return ReportSection(section_id=section_id, title=title, summary=summary,
                         claims=supported, status=status)


def compose_expert_report(state: dict[str, Any]) -> dict[str, Any]:
    resolved = state.get("resolved_entities", {})
    if len(resolved) != 1:
        raise ValueError("专家报告 Skill 只支持一个已完成消歧的专家实体")
    entity_name, entity_id = next(iter(resolved.items()))
    options = state.get("skill_input", {})
    top_n = int(options.get("top_n", 10))
    evidence_map = {item["evidence_id"]: item for item in state.get("evidence", [])
                    if item.get("evidence_id")}

    talent = state.get("talent_result")
    profiles = _rows(talent, "get_person_profile")
    education = _rows(talent, "get_education_history")
    employment = _rows(talent, "get_employment_history")
    profile_claims: list[ReportClaim | None] = []
    for row in profiles[:1]:
        profile_evidence = next((item for item in evidence_map.values()
                                 if item.get("source_tool") == "get_person_profile"), None)
        supported_row = row | ({"evidence_id": profile_evidence["evidence_id"]}
                               if profile_evidence else {})
        organization = row.get("organization") or "机构信息未记录"
        title = row.get("title") or "职称信息未记录"
        profile_claims.append(_claim(f"{entity_name}当前画像记录为：{organization}，{title}。",
                                     [supported_row], evidence_map))
    for row in education[:top_n]:
        period = f"{row.get('start_year', '未知')}-{row.get('end_year', '至今')}"
        profile_claims.append(_claim(
            f"教育经历：{row.get('institution', '未知机构')}，{row.get('degree', '学位未记录')}（{period}）。",
            [row], evidence_map,
        ))
    for row in employment[:top_n]:
        period = f"{row.get('start_year', '未知')}-{row.get('end_year') or '至今'}"
        profile_claims.append(_claim(
            f"任职经历：{row.get('organization', '未知机构')}，担任{row.get('role', '职务未记录')}（{period}）。",
            [row], evidence_map,
        ))

    achievement = state.get("achievement_result")
    papers = _rows(achievement, "get_author_papers")[:top_n]
    patents = _rows(achievement, "get_person_patents")[:top_n]
    achievement_claims: list[ReportClaim | None] = []
    if papers:
        achievement_claims.append(_claim(f"当前数据源返回 {len(papers)} 篇代表性论文（最多展示 {top_n} 篇）。",
                                         papers, evidence_map))
    for row in papers:
        achievement_claims.append(_claim(
            f"论文《{row.get('title', '题名未记录')}》（{row.get('year', '年份未记录')}）。", [row], evidence_map))
    if patents:
        achievement_claims.append(_claim(f"当前数据源返回 {len(patents)} 项代表性专利（最多展示 {top_n} 项）。",
                                         patents, evidence_map))
    for row in patents:
        achievement_claims.append(_claim(
            f"专利《{row.get('title', '名称未记录')}》。", [row], evidence_map))

    enterprise = state.get("enterprise_result")
    enterprise_claims: list[ReportClaim | None] = []
    for row in _rows(enterprise, "get_person_company_roles")[:top_n]:
        if row.get("entity_id") and row.get("entity_id") != entity_id:
            continue
        enterprise_claims.append(_claim(
            f"企业关系：在 {row.get('company_id', '企业未记录')} 担任{row.get('role', '角色未记录')}。", [row], evidence_map))
    for row in _rows(enterprise, "get_company_projects")[:top_n]:
        if entity_id not in row.get("participant_ids", []):
            continue
        enterprise_claims.append(_claim(
            f"参与企业项目《{row.get('name', row.get('project_id', '名称未记录'))}》。", [row], evidence_map))
    for row in _rows(enterprise, "get_company_patents")[:top_n]:
        if entity_id not in row.get("inventor_ids", []):
            continue
        enterprise_claims.append(_claim(
            f"关联企业专利《{row.get('title', '名称未记录')}》。", [row], evidence_map))

    graph_result = state.get("graph_result")
    graph_claims: list[ReportClaim | None] = []
    for row in _rows(graph_result, "get_neighbors")[:top_n]:
        other = row.get("target") if row.get("source") == entity_id else row.get("source")
        graph_claims.append(_claim(
            f"关系网络记录：与 {other or '未知实体'} 存在 {row.get('relation', '未命名关系')} 关系。", [row], evidence_map))

    web_result = state.get("web_result")
    web_claims: list[ReportClaim | None] = []
    web_evidence = [item for item in evidence_map.values() if item.get("source_tool") == "search_web"][:top_n]
    for item in web_evidence:
        content = item.get("content", {})
        web_claims.append(ReportClaim(
            text=f"公开来源候选记录：《{content.get('title', '标题未记录')}》。",
            evidence_ids=[item["evidence_id"]], confidence=0.75,
        ))

    sections = [
        _section("profile_history", "基础画像与履历", profile_claims,
                 errors=(talent or {}).get("errors", [])),
        _section("research_achievements", "科研成果", achievement_claims,
                 errors=(achievement or {}).get("errors", [])),
        _section("enterprise_relations", "企业与产业关联", enterprise_claims,
                 requested=bool(options.get("include_enterprise")), errors=(enterprise or {}).get("errors", [])),
        _section("cooperation_network", "合作与关联网络", graph_claims,
                 requested=bool(options.get("include_cooperation_network")), errors=(graph_result or {}).get("errors", [])),
        _section("external_sources", "公开来源补充", web_claims,
                 requested=bool(options.get("include_web")), errors=(web_result or {}).get("errors", [])),
    ]
    final_sections = [item for item in sections if item is not None]
    gaps = [f"{item.title}：{item.summary}" for item in final_sections if item.status != "complete"]
    gaps.extend(state.get("validation_result", {}).get("warnings", []))
    all_claims = [claim for section in final_sections for claim in section.claims]
    used_ids = list(dict.fromkeys(evidence_id for claim in all_claims for evidence_id in claim.evidence_ids))
    strengths: list[ReportClaim] = []
    for section in final_sections:
        if section.claims and section.status != "unavailable":
            section_ids = list(dict.fromkeys(eid for claim in section.claims for eid in claim.evidence_ids))
            strengths.append(ReportClaim(
                text=f"{section.title}已有 {len(section.claims)} 条可追溯陈述。",
                evidence_ids=section_ids,
                confidence=min(claim.confidence for claim in section.claims),
            ))
    coverage = 1.0 if all_claims and all(claim.evidence_ids for claim in all_claims) else 0.0
    report = ExpertReport(
        skill_version=EXPERT_REPORT_SPEC.version,
        entity_id=entity_id,
        entity_name=entity_name,
        report_type=options.get("report_type", "comprehensive"),
        audience=options.get("audience", "internal"),
        executive_summary=(f"本报告围绕 {entity_name} 汇总 {len(final_sections)} 个主题，形成 "
                           f"{len(all_claims)} 条可追溯陈述；扩展域缺失时按降级策略保留核心报告。"),
        sections=final_sections,
        strengths=strengths,
        risks_and_gaps=list(dict.fromkeys(gaps)) or ["未发现影响当前报告生成的结构化数据缺口。"],
        evidence_ids=used_ids,
        evidence_coverage=coverage,
        evidence_catalog=[evidence_map[evidence_id] for evidence_id in used_ids],
    )
    return report.model_dump()


def render_expert_report(report: dict[str, Any]) -> str:
    catalog = {item["evidence_id"]: item for item in report.get("evidence_catalog", [])}
    ordered_ids = report.get("evidence_ids", [])
    references = {evidence_id: index for index, evidence_id in enumerate(ordered_ids, 1)}

    def citation(ids: list[str]) -> str:
        numbers = [str(references[item]) for item in ids if item in references]
        return f"〔证据 {','.join(numbers)}〕" if numbers else ""

    lines = [
        f"# {report['entity_name']}专家报告",
        "",
        f"- 实体 ID：{report['entity_id']}",
        f"- 报告类型：{report['report_type']}",
        f"- 面向对象：{report['audience']}",
        f"- Skill 版本：{report['skill_version']}",
        f"- 证据覆盖率：{report['evidence_coverage']:.0%}",
        "",
        "## 执行摘要",
        "",
        report["executive_summary"],
    ]
    for section in report.get("sections", []):
        lines.extend(["", f"## {section['title']}", "", f"> 状态：{section['status']}。{section['summary']}"])
        for claim in section.get("claims", []):
            lines.append(f"- {claim['text']} {citation(claim['evidence_ids'])}")
    lines.extend(["", "## 证据化优势摘要", ""])
    lines.extend(f"- {item['text']} {citation(item['evidence_ids'])}" for item in report.get("strengths", []))
    lines.extend(["", "## 风险与数据缺口", ""])
    lines.extend(f"- {item}" for item in report.get("risks_and_gaps", []))
    lines.extend(["", "## 证据目录", ""])
    for evidence_id in ordered_ids:
        item = catalog[evidence_id]
        lines.append(f"{references[evidence_id]}. {item.get('source_name', '未知来源')} / "
                     f"{item.get('source_record_id', evidence_id)}（{item.get('fact_type', 'unknown')}）")
    if any(item.get("source_type") == "web" for item in catalog.values()):
        lines.extend(["", "> 公开网页内容仅作为候选证据，未自动写回知识图谱。"])
    return "\n".join(lines)
