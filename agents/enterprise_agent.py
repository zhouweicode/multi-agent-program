"""企业关系 Specialist Agent。"""
from agents.base import ToolCallingDomainAgent
from models.llm import ModelFactory
from tools.provider import get_tools


def build_enterprise_agent() -> ToolCallingDomainAgent:
    return ToolCallingDomainAgent("enterprise_agent", ModelFactory.tool_calling_model("enterprise"),
                                  get_tools("enterprise"))
