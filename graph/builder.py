"""第一阶段 LangGraph StateGraph 构建器。"""
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from graph.state import GraphRAGState
from graph.routing import after_resolution, planned_agents, after_rule_validation, after_verification
from nodes.router_node import router_node
from nodes.entity_resolution_node import entity_resolution_node
from nodes.supervisor_node import supervisor_node
from nodes.agent_nodes import (talent_agent_node, achievement_agent_node, enterprise_agent_node,
                               industry_agent_node, graph_reasoning_agent_node)
from nodes.merge_node import merge_node
from nodes.validator_node import validator_node
from nodes.answer_node import answer_node
from nodes.verification_node import verification_agent_node
from services.observability import traced_node


def build_graph(checkpointer=None):
    graph = StateGraph(GraphRAGState)
    graph.add_node("router", traced_node("router", router_node))
    graph.add_node("entity_resolution", traced_node("entity_resolution", entity_resolution_node))
    graph.add_node("supervisor", traced_node("supervisor", supervisor_node))
    graph.add_node("talent_agent", traced_node("talent_agent", talent_agent_node))
    graph.add_node("achievement_agent", traced_node("achievement_agent", achievement_agent_node))
    graph.add_node("enterprise_agent", traced_node("enterprise_agent", enterprise_agent_node))
    graph.add_node("industry_agent", traced_node("industry_agent", industry_agent_node))
    graph.add_node("graph_reasoning_agent", traced_node("graph_reasoning_agent", graph_reasoning_agent_node))
    graph.add_node("merge", traced_node("merge", merge_node))
    graph.add_node("validator", traced_node("validator", validator_node))
    graph.add_node("answer", traced_node("answer", answer_node))
    graph.add_node("verification_agent", traced_node("verification_agent", verification_agent_node))
    graph.add_edge(START, "router")
    graph.add_edge("router", "entity_resolution")
    graph.add_conditional_edges("entity_resolution", after_resolution,
                                {"supervisor": "supervisor", "talent": "talent_agent", "achievement": "achievement_agent",
                                 "enterprise": "enterprise_agent", "industry": "industry_agent", "graph": "graph_reasoning_agent"})
    graph.add_conditional_edges("supervisor", planned_agents,
                                ["talent_agent", "achievement_agent", "enterprise_agent", "industry_agent", "graph_reasoning_agent"])
    graph.add_edge("talent_agent", "merge")
    graph.add_edge("achievement_agent", "merge")
    graph.add_edge("enterprise_agent", "merge")
    graph.add_edge("industry_agent", "merge")
    graph.add_edge("graph_reasoning_agent", "merge")
    graph.add_edge("merge", "validator")
    graph.add_conditional_edges("validator", after_rule_validation,
                                {"supervisor": "supervisor", "verification_agent": "verification_agent", "answer": "answer"})
    graph.add_conditional_edges("verification_agent", after_verification,
                                {"supervisor": "supervisor", "answer": "answer"})
    graph.add_edge("answer", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())
