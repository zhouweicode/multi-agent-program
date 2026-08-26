"""公开网页研究 Specialist Agent。"""
from agents.base import ToolCallingDomainAgent
from models.llm import ModelFactory
from tools.provider import get_tools


def build_web_research_agent() -> ToolCallingDomainAgent:
    """只授权联网搜索工具，避免开放网络能力泄漏给其他领域 Agent。"""
    return ToolCallingDomainAgent(
        "web_research_agent",
        ModelFactory.tool_calling_model("web"),
        get_tools("web"),
        max_steps=4,
        max_tool_calls=3,
        final_response_instruction=(
            "最终消息必须直接回答用户问题：第一句先给明确结论，随后用一到两句说明依据。"
            "只使用搜索结果，不得补造事实；不要粘贴网页原文，不要输出原始URL或证据编号，"
            "不要讨论工作流，全文控制在220个汉字以内。"
        ),
    )
