"""图关系推理 Specialist Agent。"""
from agents.base import ToolCallingDomainAgent
from models.llm import ModelFactory
from tools.graph_tools import get_neighbors, find_path, k_hop_expand, calculate_path_strength


def build_graph_reasoning_agent() -> ToolCallingDomainAgent:
    return ToolCallingDomainAgent("graph_reasoning_agent", ModelFactory.tool_calling_model("graph"),
                                  [get_neighbors, find_path, k_hop_expand, calculate_path_strength])
