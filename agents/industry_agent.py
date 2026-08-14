"""产业链 Specialist Agent。"""
from agents.base import ToolCallingDomainAgent
from models.llm import ModelFactory
from tools.industry_tools import get_chain_structure, get_node_companies, get_node_events, rank_top_events


def build_industry_agent() -> ToolCallingDomainAgent:
    return ToolCallingDomainAgent("industry_agent", ModelFactory.tool_calling_model("industry"),
                                  [get_chain_structure, get_node_companies, get_node_events, rank_top_events])
