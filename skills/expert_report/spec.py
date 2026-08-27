"""专家报告 Skill 元数据与输入归一化。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from skills.base import SkillSpec

EXPERT_REPORT_SPEC = SkillSpec(
    skill_id="expert_report",
    version="1.0.0",
    name="专家报告",
    description="基于已解析专家实体和可追溯领域证据，生成可降级、可审计的结构化专家报告。",
    trigger_phrases=("专家报告", "专家评估报告", "人才评估报告", "专家调研报告"),
    required_capabilities=("expert_profile_history", "research_achievements"),
    optional_capabilities=("enterprise_relations", "cooperation_network", "external_public_evidence"),
    output_schema="ExpertReport",
    instruction_path=Path(__file__).with_name("SKILL.md"),
    default_input={
        "report_type": "comprehensive",
        "audience": "internal",
        "include_enterprise": True,
        "include_cooperation_network": True,
        "include_web": False,
        "top_n": 10,
    },
)


def normalize_expert_report_input(question: str, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    values = dict(EXPERT_REPORT_SPEC.default_input)
    values.update(raw or {})
    if any(word in question for word in ("简版", "简要", "摘要版", "精简")):
        values.update({"report_type": "brief", "include_enterprise": False,
                       "include_cooperation_network": False})
    if any(word in question for word in ("政府", "主管部门", "政策")):
        values["audience"] = "government"
    elif any(word in question for word in ("企业", "用人单位", "招聘")):
        values["audience"] = "enterprise"
    if any(word in question for word in ("不含企业", "不要企业", "不包含企业")):
        values["include_enterprise"] = False
    if any(word in question for word in ("不含网络", "不要关系网络", "不包含合作网络")):
        values["include_cooperation_network"] = False
    if any(word in question for word in ("联网", "网络搜索", "外部来源", "公开资料", "官网", "新闻", "最新", "查证")):
        values["include_web"] = True
    top_n = re.search(r"(?:前|TOP\s*)(\d+)", question, re.IGNORECASE)
    if top_n:
        values["top_n"] = int(top_n.group(1))
    values["report_type"] = "brief" if values.get("report_type") == "brief" else "comprehensive"
    values["audience"] = str(values.get("audience") or "internal")
    values["top_n"] = max(1, min(int(values.get("top_n", 10)), 50))
    for key in ("include_enterprise", "include_cooperation_network", "include_web"):
        values[key] = bool(values.get(key))
    return values


def selected_capabilities(skill_input: dict[str, Any], web_search_enabled: bool) -> list[str]:
    capabilities = list(EXPERT_REPORT_SPEC.required_capabilities)
    if skill_input.get("include_enterprise"):
        capabilities.append("enterprise_relations")
    if skill_input.get("include_cooperation_network"):
        capabilities.append("cooperation_network")
    if skill_input.get("include_web") and web_search_enabled:
        capabilities.append("external_public_evidence")
    return capabilities
