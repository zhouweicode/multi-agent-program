"""Merge Node：汇总领域结果、证据与任务历史。"""
from graph.state import GraphRAGState


def merge_node(state: GraphRAGState) -> dict:
    results = [state.get(field) for field in ("talent_result", "achievement_result", "enterprise_result",
                                               "industry_result", "graph_result")]
    evidence = [e for result in results if result for e in result.get("evidence", [])]
    history = list(state.get("task_history", []))
    history.extend({"agent": r["agent"], "status": "error" if r.get("errors") else "completed"} for r in results if r)
    return {"evidence": evidence, "task_history": history}
