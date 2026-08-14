"""将 Verification Agent 接入 LangGraph 的薄 Node。"""
from graph.state import GraphRAGState
from agents.verification_agent import build_verification_agent
from services.observability import emit_event


def verification_agent_node(state: GraphRAGState) -> dict:
    result = build_verification_agent().run(
        question=state["question"],
        entity_ids=list(state.get("resolved_entities", {}).values()),
        evidence_ids=[item["evidence_id"] for item in state.get("evidence", [])],
    )
    history = list(state.get("task_history", []))
    history.append({"agent": "verification_agent", "status": result["status"]})
    emit_event("VERIFICATION_COMPLETED", thread_id=state.get("thread_id"), status=result["status"], confidence=result["confidence"],
               needs_replan=result["needs_replan"])
    return {"verification_result": result, "task_history": history}
