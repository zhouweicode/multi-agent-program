"""Supervisor 任务验收契约；兼容导出由 ToolRegistry 统一生成。"""

from tools.registry import tool_registry

AGENT_DOMAINS = tool_registry.agent_domains
FACT_TYPE_TO_TOOL = tool_registry.fact_type_to_tool
DEFAULT_REQUIRED_FACT_TYPES = tool_registry.default_required_fact_types


def required_fact_types(agent: str, question: str) -> list[str]:
    """根据明确任务语义细化静态领域契约；专利查询不强制返回论文和项目。"""
    if (
        agent == "achievement_agent"
        and "专利" in question
        and not any(word in question for word in ("论文", "学术", "科研项目"))
    ):
        return ["common_patents"]
    return list(DEFAULT_REQUIRED_FACT_TYPES[agent])
