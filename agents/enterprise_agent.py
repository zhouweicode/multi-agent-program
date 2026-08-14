"""企业关系 Specialist Agent。"""
from agents.base import ToolCallingDomainAgent
from models.llm import ModelFactory
from tools.enterprise_tools import get_person_company_roles, get_company_projects, get_company_patents


def build_enterprise_agent() -> ToolCallingDomainAgent:
    return ToolCallingDomainAgent("enterprise_agent", ModelFactory.tool_calling_model("enterprise"),
                                  [get_person_company_roles, get_company_projects, get_company_patents])
