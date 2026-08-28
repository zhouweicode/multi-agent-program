"""专家报告 Skill 的确定性 Composer Node。"""

from graph.state import GraphRAGState
from services.observability import emit_event
from skills.expert_report import compose_expert_report, render_expert_report
from skills.registry import skill_registry


def expert_report_node(state: GraphRAGState) -> dict:
    spec = skill_registry.get("expert_report")
    report = spec.validate_output(compose_expert_report(state))
    markdown = render_expert_report(report)
    emit_event(
        "EXPERT_REPORT_GENERATED",
        thread_id=state.get("thread_id"),
        entity_id=report["entity_id"],
        section_count=len(report["sections"]),
        claim_count=sum(len(item["claims"]) for item in report["sections"]),
        evidence_count=len(report["evidence_ids"]),
        evidence_coverage=report["evidence_coverage"],
        skill_content_hash=spec.content_hash,
    )
    return {"report_draft": report, "report_markdown": markdown}
