"""人才机构关系 Specialist Agent。"""
from agents.base import ToolCallingDomainAgent
from models.llm import ModelFactory
from tools.provider import get_tools


def build_talent_agent() -> ToolCallingDomainAgent:
    return ToolCallingDomainAgent("talent_agent", ModelFactory.tool_calling_model("talent"),
                                  get_tools("talent"))
