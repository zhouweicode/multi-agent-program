"""将领域 Agent 接入图的薄 Node；业务推理由 Agent 与其工具完成。"""
from graph.state import GraphRAGState
from agents.talent_agent import build_talent_agent
from agents.achievement_agent import build_achievement_agent
from agents.enterprise_agent import build_enterprise_agent
from agents.industry_agent import build_industry_agent
from agents.graph_reasoning_agent import build_graph_reasoning_agent


def _goal(state: GraphRAGState, agent_name: str) -> str:
    task = next((x for x in state.get("tasks", []) if x["agent"] == agent_name), None)
    return task["goal"] if task else state["question"]


def talent_agent_node(state: GraphRAGState) -> dict:
    result = build_talent_agent().run(_goal(state, "talent_agent"), state["resolved_entities"])
    return {"talent_result": result}


def achievement_agent_node(state: GraphRAGState) -> dict:
    result = build_achievement_agent().run(_goal(state, "achievement_agent"), state["resolved_entities"])
    return {"achievement_result": result}


def enterprise_agent_node(state: GraphRAGState) -> dict:
    result = build_enterprise_agent().run(_goal(state, "enterprise_agent"), state.get("resolved_entities", {}))
    return {"enterprise_result": result}


def industry_agent_node(state: GraphRAGState) -> dict:
    result = build_industry_agent().run(_goal(state, "industry_agent"), state.get("resolved_entities", {}))
    return {"industry_result": result}


def graph_reasoning_agent_node(state: GraphRAGState) -> dict:
    result = build_graph_reasoning_agent().run(_goal(state, "graph_reasoning_agent"), state.get("resolved_entities", {}))
    return {"graph_result": result}
