"""将领域 Agent 接入图的薄 Node；业务推理由 Agent 与其工具完成。"""
from agents.achievement_agent import build_achievement_agent
from agents.enterprise_agent import build_enterprise_agent
from agents.graph_reasoning_agent import build_graph_reasoning_agent
from agents.industry_agent import build_industry_agent
from agents.talent_agent import build_talent_agent
from agents.web_research_agent import build_web_research_agent
from graph.state import GraphRAGState


def _goal(state: GraphRAGState, agent_name: str) -> str:
    task = next((x for x in state.get("tasks", []) if x["agent"] == agent_name), None)
    return task["goal"] if task else state["question"]


def talent_agent_node(state: GraphRAGState) -> dict:
    result = build_talent_agent().run(_goal(state, "talent_agent"), state["resolved_entities"], state.get("thread_id"))
    return {"talent_result": result}


def achievement_agent_node(state: GraphRAGState) -> dict:
    result = build_achievement_agent().run(_goal(state, "achievement_agent"), state["resolved_entities"], state.get("thread_id"))
    return {"achievement_result": result}


def enterprise_agent_node(state: GraphRAGState) -> dict:
    result = build_enterprise_agent().run(_goal(state, "enterprise_agent"), state.get("resolved_entities", {}), state.get("thread_id"))
    return {"enterprise_result": result}


def industry_agent_node(state: GraphRAGState) -> dict:
    result = build_industry_agent().run(_goal(state, "industry_agent"), state.get("resolved_entities", {}), state.get("thread_id"))
    return {"industry_result": result}


def graph_reasoning_agent_node(state: GraphRAGState) -> dict:
    result = build_graph_reasoning_agent().run(_goal(state, "graph_reasoning_agent"), state.get("resolved_entities", {}), state.get("thread_id"))
    return {"graph_result": result}


def web_research_agent_node(state: GraphRAGState) -> dict:
    if not state.get("web_search_enabled", True):
        return {"web_result": {
            "agent": "web_research_agent",
            "summary": "联网搜索已关闭，未调用任何外部工具",
            "facts": [],
            "evidence": [],
            "tool_calls": [],
            "errors": ["联网搜索已关闭；请在前端开启后重试"],
        }}
    goal = _goal(state, "web_research_agent")
    if goal != state["question"]:
        goal = f"{goal}\n必须回答的用户原始问题：{state['question']}"
    result = build_web_research_agent().run(
        goal,
        state.get("resolved_entities", {}),
        state.get("thread_id"),
    )
    return {"web_result": result}
