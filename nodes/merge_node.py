"""Merge Node：汇总领域结果、证据与任务历史。"""
from graph.state import GraphRAGState
from services.observability import emit_event


def merge_node(state: GraphRAGState) -> dict:
    results = [state.get(field) for field in ("talent_result", "achievement_result", "enterprise_result",
                                               "industry_result", "graph_result")]
    evidence_map = {item["evidence_id"]: item
                    for result in results if result for item in result.get("evidence", [])}
    evidence = list(evidence_map.values())
    history = list(state.get("task_history", []))
    results_by_agent = {result["agent"]: result for result in results if result}
    attempt = state.get("replan_count", 0)
    for task in state.get("tasks", []):
        result = results_by_agent.get(task["agent"])
        entry = {"task_id": task["task_id"], "agent": task["agent"], "attempt": attempt,
                 "status": "error" if result and result.get("errors") else "completed"}
        if not any(item.get("task_id") == entry["task_id"] and item.get("attempt") == attempt for item in history):
            history.append(entry)
    emit_event("MERGE_COMPLETED", thread_id=state.get("thread_id"),
               result_count=sum(1 for result in results if result), evidence_count=len(evidence))
    return {"evidence": evidence, "task_history": history}
