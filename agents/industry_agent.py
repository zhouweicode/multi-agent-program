"""产业链 Specialist Agent。"""
from agents.base import ToolCallingDomainAgent
from models.llm import ModelFactory
from tools.provider import get_tools


def build_industry_agent() -> ToolCallingDomainAgent:
    return ToolCallingDomainAgent("industry_agent", ModelFactory.tool_calling_model("industry"),
                                  get_tools("industry"))
