"""人才机构关系 Specialist Agent。"""
from agents.base import ToolCallingDomainAgent
from models.llm import ModelFactory
from tools.talent_tools import get_person_profile, get_employment_history, match_employment_overlap


def build_talent_agent() -> ToolCallingDomainAgent:
    return ToolCallingDomainAgent("talent_agent", ModelFactory.tool_calling_model("talent"),
                                  [get_person_profile, get_employment_history, match_employment_overlap])

