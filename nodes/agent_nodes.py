"""将领域 Agent 接入图的薄 Node；业务推理由 Agent 与其工具完成。"""
from agents.achievement_agent import build_achievement_agent
from agents.enterprise_agent import build_enterprise_agent
from agents.graph_reasoning_agent import build_graph_reasoning_agent
from agents.industry_agent import build_industry_agent
from agents.talent_agent import build_talent_agent
from agents.web_research_agent import build_web_research_agent
from graph.routing import task_completion_key
from graph.state import GraphRAGState


def _task(state: GraphRAGState, agent_name: str) -> dict | None:
    return next((x for x in state.get("tasks", []) if x["agent"] == agent_name), None)


def _goal(state: GraphRAGState, agent_name: str) -> str:
    task = _task(state, agent_name)
    return task["goal"] if task else state["question"]


def _update(state: GraphRAGState, agent_name: str, result_field: str, result: dict) -> dict:
    task = _task(state, agent_name)
    completion = ([task_completion_key(task["task_id"], state.get("replan_count", 0))]
                  if task else [])
    return {result_field: result, "task_completions": completion}


def talent_agent_node(state: GraphRAGState) -> dict:
    result = build_talent_agent().run(_goal(state, "talent_agent"), state["resolved_entities"], state.get("thread_id"))
    return _update(state, "talent_agent", "talent_result", result)


def achievement_agent_node(state: GraphRAGState) -> dict:
    result = build_achievement_agent().run(_goal(state, "achievement_agent"), state["resolved_entities"], state.get("thread_id"))
    return _update(state, "achievement_agent", "achievement_result", result)


def enterprise_agent_node(state: GraphRAGState) -> dict:
    result = build_enterprise_agent().run(_goal(state, "enterprise_agent"), state.get("resolved_entities", {}), state.get("thread_id"))
    return _update(state, "enterprise_agent", "enterprise_result", result)


def industry_agent_node(state: GraphRAGState) -> dict:
    result = build_industry_agent().run(_goal(state, "industry_agent"), state.get("resolved_entities", {}), state.get("thread_id"))
    return _update(state, "industry_agent", "industry_result", result)


def graph_reasoning_agent_node(state: GraphRAGState) -> dict:
    result = build_graph_reasoning_agent().run(_goal(state, "graph_reasoning_agent"), state.get("resolved_entities", {}), state.get("thread_id"))
    return _update(state, "graph_reasoning_agent", "graph_result", result)


def web_research_agent_node(state: GraphRAGState) -> dict:
    if not state.get("web_search_enabled", True):
        result = {
            "agent": "web_research_agent",
            "summary": "联网搜索已关闭，未调用任何外部工具",
            "facts": [],
            "evidence": [],
            "tool_calls": [],
            "errors": ["联网搜索已关闭；请在前端开启后重试"],
        }
        return _update(state, "web_research_agent", "web_result", result)
    goal = _goal(state, "web_research_agent")
    if goal != state["question"]:
        goal = f"{goal}\n必须回答的用户原始问题：{state['question']}"
    result = build_web_research_agent().run(
        goal,
        state.get("resolved_entities", {}),
        state.get("thread_id"),
    )
    return _update(state, "web_research_agent", "web_result", result)
