"""科研成果 Specialist Agent。"""
from agents.base import ToolCallingDomainAgent
from models.llm import ModelFactory
from tools.provider import get_tools


def build_achievement_agent() -> ToolCallingDomainAgent:
    return ToolCallingDomainAgent("achievement_agent", ModelFactory.tool_calling_model("achievement"),
                                  get_tools("achievement"))
