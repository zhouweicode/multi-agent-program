"""条件路由函数。第一阶段仅开放 talent/achievement 两个领域。"""
from graph.state import GraphRAGState


def after_resolution(state: GraphRAGState) -> str:
    return "supervisor" if state.get("complexity") == "complex" else state.get("primary_domain", "achievement")


def planned_agents(state: GraphRAGState) -> list[str]:
    """按 Supervisor 的结构化任务动态 fan-out。"""
    return [task["agent"] for task in state.get("tasks", [])]


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
