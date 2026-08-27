"""LangGraph 条件路由与依赖感知任务调度。"""
from graph.state import GraphRAGState
from services.observability import emit_event


def after_resolution(state: GraphRAGState) -> str:
    return "supervisor" if state.get("complexity") == "complex" else state.get("primary_domain", "achievement")


def planned_agents(state: GraphRAGState) -> list[str]:
    """兼容旧调用：返回计划内全部 Agent。"""
    return [task["agent"] for task in state.get("tasks", [])]


def task_completion_key(task_id: str, generation: int) -> str:
    return f"{generation}:{task_id}"


def scheduled_agents(state: GraphRAGState) -> str | list[str]:
    """按依赖关系分波调度；sequential 每波一个任务，parallel 执行全部 ready 任务。"""
    tasks = state.get("tasks", [])
    if not tasks:
        return "merge"
    generation = state.get("replan_count", 0)
    completed = set(state.get("task_completions", []))
    task_ids = {task["task_id"] for task in tasks}
    for task in tasks:
        unknown = set(task.get("depends_on", [])) - task_ids
        if unknown:
            raise ValueError(f"任务 {task['task_id']} 依赖不存在的任务: {', '.join(sorted(unknown))}")
    pending = [task for task in tasks
               if task_completion_key(task["task_id"], generation) not in completed]
    if not pending:
        emit_event("TASK_SCHEDULING_COMPLETED", thread_id=state.get("thread_id"),
                   execution_mode=state.get("plan", {}).get("execution_mode", "parallel"),
                   task_count=len(tasks), generation=generation)
        return "merge"
    ready = [task for task in pending if all(
        task_completion_key(dependency, generation) in completed
        for dependency in task.get("depends_on", [])
    )]
    if not ready:
        raise ValueError("任务依赖图存在环，或依赖任务未产生完成信号")
    mode = state.get("plan", {}).get("execution_mode", "parallel")
    selected = ready[:1] if mode == "sequential" else ready
    agents = [task["agent"] for task in selected]
    emit_event("TASKS_DISPATCHED", thread_id=state.get("thread_id"), execution_mode=mode,
               generation=generation, task_ids=[task["task_id"] for task in selected], agents=agents,
               pending_count=len(pending))
    return agents


def after_rule_validation(state: GraphRAGState) -> str:
    """规则失败优先重规划；语义问题才进入第二层验证。"""
    validation = state.get("validation_result", {})
    if validation.get("needs_replan") and state.get("replan_count", 0) < state.get("max_replans", 2):
        return "supervisor"
    return "verification_agent" if state.get("requires_verification") and validation.get("valid") else "answer"


def after_verification(state: GraphRAGState) -> str:
    """只有证据不足型 FAIL 才重规划；约束不满足型 FAIL 可直接回答。"""
    result = state.get("verification_result", {})
    if result.get("needs_replan") and state.get("replan_count", 0) < state.get("max_replans", 2):
        return "supervisor"
    return "answer"
