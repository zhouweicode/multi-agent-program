"""将 Verification Agent 接入 LangGraph 的薄 Node。"""
from graph.state import GraphRAGState
from agents.verification_agent import build_verification_agent


def verification_agent_node(state: GraphRAGState) -> dict:
    result = build_verification_agent().run(
        question=state["question"],
        entity_ids=list(state.get("resolved_entities", {}).values()),
        evidence_ids=[item["evidence_id"] for item in state.get("evidence", [])],
    )
    history = list(state.get("task_history", []))
    history.append({"agent": "verification_agent", "status": result["status"]})
    return {"verification_result": result, "task_history": history}
