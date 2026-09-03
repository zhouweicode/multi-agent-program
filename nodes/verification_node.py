"""将 Verification Agent 接入 LangGraph 的薄 Node。"""
from agents.verification_agent import build_verification_agent
from graph.state import GraphRAGState
from services.observability import emit_event


def verification_agent_node(state: GraphRAGState) -> dict:
    from agents.verification_policies import get_verification_policy

    policy = get_verification_policy(
        state.get("verification_claim_type"), state["question"]
    )
    evidence_records = [
        item for item in state.get("evidence", [])
        if item.get("source_tool") in set(policy.source_tools)
    ]
    evidence_ids = [item["evidence_id"] for item in evidence_records]
    result = build_verification_agent().run(
        question=state["question"],
        entity_ids=list(state.get("resolved_entities", {}).values()),
        evidence_ids=evidence_ids,
        evidence_records=evidence_records,
        claim_type=policy.claim_type,
        thread_id=state.get("thread_id"),
    )
    history = list(state.get("task_history", []))
    history.append({"agent": "verification_agent", "status": result["status"]})
    emit_event("VERIFICATION_COMPLETED", thread_id=state.get("thread_id"), status=result["status"], confidence=result["confidence"],
               needs_replan=result["needs_replan"])
    return {"verification_result": result, "task_history": history}
