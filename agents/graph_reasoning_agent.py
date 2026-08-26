"""图关系推理 Specialist Agent。"""
from agents.base import ToolCallingDomainAgent
from models.llm import ModelFactory
from tools.provider import get_tools


def build_graph_reasoning_agent() -> ToolCallingDomainAgent:
    return ToolCallingDomainAgent("graph_reasoning_agent", ModelFactory.tool_calling_model("graph"),
                                  get_tools("graph"))
