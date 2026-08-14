"""科研成果 Specialist Agent。"""
from agents.base import ToolCallingDomainAgent
from models.llm import ModelFactory
from tools.achievement_tools import get_author_papers, get_common_papers, aggregate_cooperation, get_common_projects


def build_achievement_agent() -> ToolCallingDomainAgent:
    return ToolCallingDomainAgent("achievement_agent", ModelFactory.tool_calling_model("achievement"),
                                  [get_author_papers, get_common_papers, aggregate_cooperation, get_common_projects])
