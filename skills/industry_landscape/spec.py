"""产业全景报告 Skill 元数据与输入归一化。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from skills.loader import load_skill_spec

INDUSTRY_LANDSCAPE_SPEC = load_skill_spec(Path(__file__).with_name("SKILL.md"))


def _industry_query(question: str) -> str:
    match = re.search(
        r"([\u4e00-\u9fffA-Za-z0-9_-]{2,30}?)(?:产业链|产业|行业)(?:全景|研究)?报告",
        question,
    )
    if not match:
        return ""
    value = match.group(1)
    for prefix in ("请生成", "生成", "请输出", "输出", "请做一份", "做一份", "一份"):
        value = value.removeprefix(prefix)
    for modifier in (
        "面向投资人的",
        "面向投资人",
        "面向政府的",
        "面向政府",
        "面向企业的",
        "面向企业",
        "简版",
        "简要",
        "摘要版",
        "精简",
        "完整",
        "综合",
    ):
        value = value.replace(modifier, "")
    value = value.strip("的 ：:，,")
    for suffix in ("产业链", "产业", "行业"):
        value = value.removesuffix(suffix)
    return value.strip()


def normalize_industry_landscape_input(
    question: str, raw: dict[str, Any] | None = None
) -> dict[str, Any]:
    values = dict(INDUSTRY_LANDSCAPE_SPEC.default_input)
    values.update(raw or {})
    if not values.get("industry_query"):
        values["industry_query"] = _industry_query(question)
    if any(word in question for word in ("简版", "简要", "摘要版", "精简")):
        values.update({"report_type": "brief", "top_n_companies": 5, "top_n_events": 5})
    if any(word in question for word in ("政府", "主管部门", "政策")):
        values["audience"] = "government"
    elif any(word in question for word in ("投资", "投资人", "投研")):
        values["audience"] = "investment"
    elif any(word in question for word in ("企业", "公司", "战略")):
        values["audience"] = "enterprise"
    if any(
        word in question
        for word in (
            "联网",
            "网络搜索",
            "外部来源",
            "公开资料",
            "官网",
            "新闻",
            "最新",
            "查证",
        )
    ):
        values["include_web"] = True
    top_n = re.search(r"(?:前|TOP\s*)(\d+)", question, re.IGNORECASE)
    if top_n:
        values["top_n_companies"] = values["top_n_events"] = int(top_n.group(1))
    values["report_type"] = (
        "brief" if values.get("report_type") == "brief" else "comprehensive"
    )
    values["audience"] = str(values.get("audience") or "internal")
    values["industry_query"] = str(values.get("industry_query") or "").strip()
    values["include_web"] = bool(values.get("include_web"))
    for key in ("top_n_companies", "top_n_events"):
        values[key] = max(1, min(int(values.get(key, 10)), 50))
    return INDUSTRY_LANDSCAPE_SPEC.validate_input(values)


def selected_capabilities(
    skill_input: dict[str, Any], web_search_enabled: bool
) -> list[str]:
    capabilities = list(INDUSTRY_LANDSCAPE_SPEC.required_capabilities)
    if skill_input.get("include_web") and web_search_enabled:
        capabilities.append("external_industry_evidence")
    return capabilities
