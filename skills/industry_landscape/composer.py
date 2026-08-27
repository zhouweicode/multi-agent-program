"""产业全景报告的确定性证据 Composer。"""
from __future__ import annotations

from typing import Any, Iterable

from skills.industry_landscape.schemas import IndustryClaim, IndustryLandscapeReport, IndustrySection
from skills.industry_landscape.spec import INDUSTRY_LANDSCAPE_SPEC


def _facts(result: dict[str, Any] | None, tool_name: str) -> list[Any]:
    return [fact.get("data") for fact in (result or {}).get("facts", []) if fact.get("tool") == tool_name]


def _rows(result: dict[str, Any] | None, tool_name: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for data in _facts(result, tool_name):
        if isinstance(data, list):
            values.extend(item for item in data if isinstance(item, dict))
        elif isinstance(data, dict) and not data.get("error"):
            values.append(data)
    return values


def _row_ids(row: dict[str, Any], tool_name: str, evidence_map: dict[str, dict[str, Any]]) -> list[str]:
    direct = row.get("evidence_ids") or ([row.get("evidence_id")] if row.get("evidence_id") else [])
    ids = [str(item) for item in direct if item in evidence_map]
    if ids:
        return list(dict.fromkeys(ids))
    record_values = {str(row[key]) for key in ("chain_id", "segment_id", "node_id", "company_id", "event_id")
                     if row.get(key) is not None}
    return [evidence_id for evidence_id, item in evidence_map.items()
            if item.get("source_tool") == tool_name and str(item.get("source_record_id")) in record_values]


def _claim(text: str, rows: Iterable[dict[str, Any]], tool_name: str,
           evidence_map: dict[str, dict[str, Any]], confidence: float = 1.0) -> IndustryClaim | None:
    ids = list(dict.fromkeys(evidence_id for row in rows
                             for evidence_id in _row_ids(row, tool_name, evidence_map)))
    return IndustryClaim(text=text, evidence_ids=ids, confidence=confidence) if ids else None


def _section(section_id: str, title: str, claims: list[IndustryClaim | None], *, requested: bool = True,
             errors: list[str] | None = None) -> IndustrySection | None:
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
    return IndustrySection(section_id=section_id, title=title, summary=summary,
                           claims=supported, status=status)


def compose_industry_landscape(state: dict[str, Any]) -> dict[str, Any]:
    options = state.get("skill_input", {})
    industry = state.get("industry_result")
    web = state.get("web_result")
    evidence_map = {item["evidence_id"]: item for item in state.get("evidence", []) if item.get("evidence_id")}
    segment_rows = _rows(industry, "search_industry_segments")
    chains = _rows(industry, "get_chain_structure")
    companies = _rows(industry, "get_node_companies")[:int(options.get("top_n_companies", 10))]
    events = (_rows(industry, "rank_top_events") or _rows(industry, "get_node_events"))[:
        int(options.get("top_n_events", 10))]

    scope_claims: list[IndustryClaim | None] = []
    for row in segment_rows[:10]:
        scope_claims.append(_claim(
            f"产业节点：{row.get('name', row.get('segment_id', '名称未记录'))}"
            f"（节点 ID：{row.get('segment_id', '未记录')}，事件记录数：{row.get('event_count', '未记录')}）。",
            [row], "search_industry_segments", evidence_map,
        ))

    structure_claims: list[IndustryClaim | None] = []
    for row in chains[:1]:
        details = row.get("node_details", [])
        structure_claims.append(_claim(
            f"{row.get('name', row.get('chain_id', '产业链'))}当前返回 {len(details)} 个关联产业节点。",
            [row], "get_chain_structure", evidence_map,
        ))
        for detail in details:
            structure_claims.append(_claim(
                f"产业链节点：{detail.get('name', detail.get('node_id', '名称未记录'))}，"
                f"层级为{detail.get('level', '未记录')}。",
                [row], "get_chain_structure", evidence_map,
            ))

    company_claims = [
        _claim(
            f"关联企业：{row.get('name', row.get('company_id', '名称未记录'))}"
            f"（企业 ID：{row.get('company_id', '未记录')}）。",
            [row], "get_node_companies", evidence_map,
        )
        for row in companies
    ]
    event_claims = [
        _claim(
            f"产业事件：《{row.get('title', '标题未记录')}》；日期 {row.get('date', '未记录')}；"
            f"重要度记录 {row.get('importance', '未记录')}。",
            [row], "rank_top_events" if _rows(industry, "rank_top_events") else "get_node_events", evidence_map,
        )
        for row in events
    ]

    web_claims: list[IndustryClaim | None] = []
    for item in [row for row in evidence_map.values() if row.get("source_tool") == "search_web"][:
            int(options.get("top_n_events", 10))]:
        content = item.get("content", {})
        web_claims.append(IndustryClaim(
            text=f"公开来源候选记录：《{content.get('title', '标题未记录')}》。",
            evidence_ids=[item["evidence_id"]], confidence=0.75,
        ))

    sections = [
        _section("industry_scope", "产业范围与节点概览", scope_claims,
                 errors=(industry or {}).get("errors", [])),
        _section("chain_structure", "产业链结构", structure_claims,
                 errors=(industry or {}).get("errors", [])),
        _section("company_landscape", "关联企业", company_claims,
                 errors=(industry or {}).get("errors", [])),
        _section("key_events", "重点产业事件", event_claims,
                 errors=(industry or {}).get("errors", [])),
        _section("external_sources", "公开来源补充", web_claims,
                 requested=bool(options.get("include_web")), errors=(web or {}).get("errors", [])),
    ]
    final_sections = [item for item in sections if item is not None]
    claims = [claim for section in final_sections for claim in section.claims]
    used_ids = list(dict.fromkeys(evidence_id for claim in claims for evidence_id in claim.evidence_ids))
    key_signals = [IndustryClaim(
        text=f"{section.title}已有 {len(section.claims)} 条可追溯记录。",
        evidence_ids=list(dict.fromkeys(eid for claim in section.claims for eid in claim.evidence_ids)),
        confidence=min(claim.confidence for claim in section.claims),
    ) for section in final_sections if section.claims]
    gaps = [f"{section.title}：{section.summary}" for section in final_sections if section.status != "complete"]
    gaps.extend(state.get("validation_result", {}).get("warnings", []))
    chain = chains[0] if chains else {}
    segment = segment_rows[0] if segment_rows else {}
    industry_id = str(chain.get("chain_id") or segment.get("segment_id") or options.get("industry_query") or "unknown")
    industry_name = str(chain.get("name") or options.get("industry_query") or segment.get("name") or "未命名产业")
    report = IndustryLandscapeReport(
        skill_version=INDUSTRY_LANDSCAPE_SPEC.version,
        industry_id=industry_id,
        industry_name=industry_name,
        report_type=options.get("report_type", "comprehensive"),
        audience=options.get("audience", "internal"),
        executive_summary=(f"本报告围绕{industry_name}汇总 {len(segment_rows)} 个候选产业节点、"
                           f"{len(companies)} 家关联企业和 {len(events)} 条重点事件；"
                           "所有分析性陈述均限定在本次返回且可追溯的数据范围内。"),
        sections=final_sections,
        key_signals=key_signals,
        risks_and_gaps=list(dict.fromkeys(gaps)) or ["未发现影响当前报告生成的结构化数据缺口。"],
        evidence_ids=used_ids,
        evidence_coverage=1.0 if claims and all(claim.evidence_ids for claim in claims) else 0.0,
        evidence_catalog=[evidence_map[evidence_id] for evidence_id in used_ids],
    )
    return report.model_dump()


def render_industry_landscape(report: dict[str, Any]) -> str:
    ordered_ids = report.get("evidence_ids", [])
    references = {evidence_id: index for index, evidence_id in enumerate(ordered_ids, 1)}
    catalog = {item["evidence_id"]: item for item in report.get("evidence_catalog", [])}

    def citation(ids: list[str]) -> str:
        numbers = [str(references[item]) for item in ids if item in references]
        return f"〔证据 {','.join(numbers)}〕" if numbers else ""

    industry_name = report["industry_name"]
    title = (f"{industry_name}全景报告" if industry_name.endswith(("产业", "产业链", "行业"))
             else f"{industry_name}产业全景报告")
    lines = [
        f"# {title}", "",
        f"- 产业 ID：{report['industry_id']}", f"- 报告类型：{report['report_type']}",
        f"- 面向对象：{report['audience']}", f"- Skill 版本：{report['skill_version']}",
        f"- 证据覆盖率：{report['evidence_coverage']:.0%}", "", "## 执行摘要", "",
        report["executive_summary"],
    ]
    for section in report.get("sections", []):
        lines.extend(["", f"## {section['title']}", "", f"> 状态：{section['status']}。{section['summary']}"])
        lines.extend(f"- {claim['text']} {citation(claim['evidence_ids'])}" for claim in section.get("claims", []))
    lines.extend(["", "## 关键数据信号", ""])
    lines.extend(f"- {item['text']} {citation(item['evidence_ids'])}" for item in report.get("key_signals", []))
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
