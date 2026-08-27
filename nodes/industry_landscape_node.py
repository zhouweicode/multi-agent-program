"""产业全景报告 Skill 的确定性 Composer Node。"""
from graph.state import GraphRAGState
from services.observability import emit_event
from skills.industry_landscape import compose_industry_landscape, render_industry_landscape


def industry_landscape_node(state: GraphRAGState) -> dict:
    report = compose_industry_landscape(state)
    markdown = render_industry_landscape(report)
    emit_event(
        "INDUSTRY_LANDSCAPE_GENERATED",
        thread_id=state.get("thread_id"), industry_id=report["industry_id"],
        section_count=len(report["sections"]),
        claim_count=sum(len(item["claims"]) for item in report["sections"]),
        evidence_count=len(report["evidence_ids"]), evidence_coverage=report["evidence_coverage"],
    )
    return {"report_draft": report, "report_markdown": markdown}
