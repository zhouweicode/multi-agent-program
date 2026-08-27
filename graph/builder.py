"""第一阶段 LangGraph StateGraph 构建器。"""
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from graph.routing import (
    after_resolution,
    after_rule_validation,
    after_verification,
    scheduled_agents,
)
from graph.state import GraphRAGState
from nodes.agent_nodes import (
    achievement_agent_node,
    enterprise_agent_node,
    graph_reasoning_agent_node,
    industry_agent_node,
    talent_agent_node,
    web_research_agent_node,
)
from nodes.answer_node import answer_node
from nodes.conversation_memory_node import (
    conversation_memory_recall_node,
    conversation_memory_writeback_node,
)
from nodes.entity_resolution_node import entity_resolution_node
from nodes.expert_report_node import expert_report_node
from nodes.merge_node import merge_node
from nodes.query_experience_node import (
    query_experience_recall_node,
    query_experience_writeback_node,
)
from nodes.router_node import router_node
from nodes.supervisor_node import supervisor_node
from nodes.validator_node import validator_node
from nodes.verification_node import verification_agent_node
from services.observability import traced_node


def build_graph(checkpointer=None):
    graph = StateGraph(GraphRAGState)
    graph.add_node("conversation_memory_recall", traced_node("conversation_memory_recall", conversation_memory_recall_node))
    graph.add_node("router", traced_node("router", router_node))
    graph.add_node("query_experience_recall", traced_node("query_experience_recall", query_experience_recall_node))
    graph.add_node("entity_resolution", traced_node("entity_resolution", entity_resolution_node))
    graph.add_node("supervisor", traced_node("supervisor", supervisor_node))
    # 调度器是纯控制节点；不生成 NODE_EXECUTED 快照，避免把空 State Update 暴露为业务步骤。
    graph.add_node("task_scheduler", lambda state: {})
    graph.add_node("talent_agent", traced_node("talent_agent", talent_agent_node))
    graph.add_node("achievement_agent", traced_node("achievement_agent", achievement_agent_node))
    graph.add_node("enterprise_agent", traced_node("enterprise_agent", enterprise_agent_node))
    graph.add_node("industry_agent", traced_node("industry_agent", industry_agent_node))
    graph.add_node("graph_reasoning_agent", traced_node("graph_reasoning_agent", graph_reasoning_agent_node))
    graph.add_node("web_research_agent", traced_node("web_research_agent", web_research_agent_node))
    graph.add_node("merge", traced_node("merge", merge_node))
    graph.add_node("validator", traced_node("validator", validator_node))
    graph.add_node("expert_report", traced_node("expert_report", expert_report_node))
    graph.add_node("answer", traced_node("answer", answer_node))
    graph.add_node("conversation_memory_writeback", traced_node("conversation_memory_writeback", conversation_memory_writeback_node))
    graph.add_node("query_experience_writeback", traced_node("query_experience_writeback", query_experience_writeback_node))
    graph.add_node("verification_agent", traced_node("verification_agent", verification_agent_node))
    graph.add_edge(START, "conversation_memory_recall")
    graph.add_edge("conversation_memory_recall", "router")
    graph.add_edge("router", "query_experience_recall")
    graph.add_edge("query_experience_recall", "entity_resolution")
    graph.add_conditional_edges("entity_resolution", after_resolution,
                                {"supervisor": "supervisor", "talent": "talent_agent", "achievement": "achievement_agent",
                                 "enterprise": "enterprise_agent", "industry": "industry_agent", "graph": "graph_reasoning_agent",
                                 "web": "web_research_agent"})
    graph.add_edge("supervisor", "task_scheduler")
    graph.add_conditional_edges("task_scheduler", scheduled_agents,
                                ["talent_agent", "achievement_agent", "enterprise_agent", "industry_agent",
                                 "graph_reasoning_agent", "web_research_agent", "merge"])
    graph.add_edge("talent_agent", "task_scheduler")
    graph.add_edge("achievement_agent", "task_scheduler")
    graph.add_edge("enterprise_agent", "task_scheduler")
    graph.add_edge("industry_agent", "task_scheduler")
    graph.add_edge("graph_reasoning_agent", "task_scheduler")
    graph.add_edge("web_research_agent", "task_scheduler")
    graph.add_edge("merge", "validator")
    graph.add_conditional_edges("validator", after_rule_validation,
                                {"supervisor": "supervisor", "verification_agent": "verification_agent",
                                 "expert_report": "expert_report", "answer": "answer"})
    graph.add_edge("expert_report", "answer")
    graph.add_conditional_edges("verification_agent", after_verification,
                                {"supervisor": "supervisor", "answer": "answer"})
    graph.add_edge("answer", "conversation_memory_writeback")
    graph.add_edge("conversation_memory_writeback", "query_experience_writeback")
    graph.add_edge("query_experience_writeback", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())
