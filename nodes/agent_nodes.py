"""将领域 Agent 接入图的薄 Node；业务推理由 Agent 与其工具完成。"""
from agents.achievement_agent import build_achievement_agent
from agents.enterprise_agent import build_enterprise_agent
from agents.graph_reasoning_agent import build_graph_reasoning_agent
from agents.industry_agent import build_industry_agent
from agents.talent_agent import build_talent_agent
from agents.web_research_agent import build_web_research_agent
from graph.routing import task_completion_key
from graph.state import GraphRAGState
from models.contracts import required_fact_types

_BUILDERS = {
    "talent_agent": build_talent_agent,
    "achievement_agent": build_achievement_agent,
    "enterprise_agent": build_enterprise_agent,
    "industry_agent": build_industry_agent,
    "graph_reasoning_agent": build_graph_reasoning_agent,
    "web_research_agent": build_web_research_agent,
}

_RESULT_FIELDS = {
    "talent_agent": "talent_result",
    "achievement_agent": "achievement_result",
    "enterprise_agent": "enterprise_result",
    "industry_agent": "industry_result",
    "graph_reasoning_agent": "graph_result",
    "web_research_agent": "web_result",
}


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


def task_executor_node(state: GraphRAGState) -> dict:
    """Execute one PlannedTask so multiple instances may target the same Agent."""
    task = state["active_task"]
    agent_name = task["agent"]
    if agent_name not in _BUILDERS:
        raise ValueError(f"未知任务 Agent: {agent_name}")
    if agent_name == "web_research_agent" and not state.get("web_search_enabled", True):
        result = {
            "agent": agent_name,
            "summary": "联网搜索已关闭，未调用任何外部工具",
            "facts": [], "evidence": [], "tool_calls": [], "tool_receipts": [],
            "errors": ["联网搜索已关闭；请在前端开启后重试"],
            "metrics": {}, "stop_reason": "POLICY_BLOCKED",
        }
    else:
        goal = task["goal"]
        if agent_name in {"graph_reasoning_agent", "web_research_agent"}:
            question = state.get("question", "")
            if question and question not in goal:
                goal = f"{goal}；必须回答的用户原始问题：{question}"
        result = _BUILDERS[agent_name]().run(
            goal,
            state.get("resolved_entities", {}),
            state.get("thread_id"),
            state.get("long_term_memory_prompt"),
            required_fact_types=task.get("required_fact_types", []),
            task_id=task["task_id"],
            preferred_tools=task.get("preferred_tools", []),
        )
    key = task_completion_key(task["task_id"], state.get("replan_count", 0))
    return {
        "task_results": {key: {"task": task, "result": result}},
        "task_completions": [key],
    }


def _combine_results(agent_name: str, entries: list[dict]) -> dict:
    results = [entry["result"] for entry in entries]
    if len(results) == 1:
        return results[0]
    metrics: dict[str, float] = {}
    for result in results:
        for name, value in result.get("metrics", {}).items():
            if isinstance(value, (int, float)):
                metrics[name] = metrics.get(name, 0) + value
    return {
        "agent": agent_name,
        "summary": f"{agent_name} 完成 {len(results)} 个任务实例",
        "response": "\n".join(filter(None, (item.get("response") for item in results))) or None,
        "facts": [fact for item in results for fact in item.get("facts", [])],
        "evidence": [evidence for item in results for evidence in item.get("evidence", [])],
        "tool_calls": [call for item in results for call in item.get("tool_calls", [])],
        "tool_receipts": [receipt for item in results for receipt in item.get("tool_receipts", [])],
        "errors": [error for item in results for error in item.get("errors", [])],
        "metrics": metrics,
        "stop_reason": "completed" if all(item.get("stop_reason") == "completed" for item in results)
        else "PARTIAL",
    }


def materialize_task_results_node(state: GraphRAGState) -> dict:
    """Build legacy per-Agent result fields from current-generation task results."""
    generation = state.get("replan_count", 0)
    by_agent: dict[str, list[dict]] = {}
    for task in state.get("tasks", []):
        key = task_completion_key(task["task_id"], generation)
        entry = state.get("task_results", {}).get(key)
        if entry:
            by_agent.setdefault(task["agent"], []).append(entry)
    return {
        _RESULT_FIELDS[agent]: _combine_results(agent, entries)
        for agent, entries in by_agent.items()
    }


def talent_agent_node(state: GraphRAGState) -> dict:
    result = build_talent_agent().run(_goal(state, "talent_agent"), state["resolved_entities"],
                                      state.get("thread_id"), state.get("long_term_memory_prompt"),
                                      required_fact_types=required_fact_types(
                                          "talent_agent", state["question"], len(state["resolved_entities"])
                                      ))
    return _update(state, "talent_agent", "talent_result", result)


def achievement_agent_node(state: GraphRAGState) -> dict:
    result = build_achievement_agent().run(_goal(state, "achievement_agent"), state["resolved_entities"],
                                           state.get("thread_id"), state.get("long_term_memory_prompt"),
                                           required_fact_types=required_fact_types(
                                               "achievement_agent", state["question"], len(state["resolved_entities"])
                                           ))
    return _update(state, "achievement_agent", "achievement_result", result)


def enterprise_agent_node(state: GraphRAGState) -> dict:
    result = build_enterprise_agent().run(_goal(state, "enterprise_agent"), state.get("resolved_entities", {}),
                                          state.get("thread_id"), state.get("long_term_memory_prompt"),
                                          required_fact_types=required_fact_types(
                                              "enterprise_agent", state["question"], len(state.get("resolved_entities", {}))
                                          ))
    return _update(state, "enterprise_agent", "enterprise_result", result)


def industry_agent_node(state: GraphRAGState) -> dict:
    result = build_industry_agent().run(_goal(state, "industry_agent"), state.get("resolved_entities", {}),
                                        state.get("thread_id"), state.get("long_term_memory_prompt"),
                                        required_fact_types=required_fact_types(
                                            "industry_agent", state["question"], len(state.get("resolved_entities", {}))
                                        ))
    return _update(state, "industry_agent", "industry_result", result)


def graph_reasoning_agent_node(state: GraphRAGState) -> dict:
    goal = _goal(state, "graph_reasoning_agent")
    if _task(state, "graph_reasoning_agent") and state["question"] not in goal:
        goal = f"{goal}；必须回答的用户原始问题：{state['question']}"
    result = build_graph_reasoning_agent().run(goal, state.get("resolved_entities", {}),
                                               state.get("thread_id"), state.get("long_term_memory_prompt"),
                                               required_fact_types=required_fact_types(
                                                   "graph_reasoning_agent", state["question"], len(state.get("resolved_entities", {}))
                                               ))
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
        state.get("long_term_memory_prompt"),
        required_fact_types=required_fact_types(
            "web_research_agent", state["question"], len(state.get("resolved_entities", {}))
        ),
    )
    return _update(state, "web_research_agent", "web_result", result)
