"""Claim-specific verification policies for the shared Verification Agent."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerificationPolicy:
    claim_type: str
    relation: str
    source_tools: tuple[str, ...]
    tool_sequence: tuple[str, ...]
    min_year_span: int = 0
    min_achievements: int = 1

    def as_dict(self) -> dict:
        return {
            "claim_type": self.claim_type,
            "relation": self.relation,
            "source_tools": list(self.source_tools),
            "tool_sequence": list(self.tool_sequence),
            "constraints": {
                "min_year_span": self.min_year_span,
                "min_achievements": self.min_achievements,
            },
        }


POLICIES = {
    "CORE_RESEARCH_PARTNER": VerificationPolicy(
        "CORE_RESEARCH_PARTNER", "CORE_RESEARCH_PARTNER",
        ("get_author_papers", "get_common_papers", "get_common_projects", "aggregate_cooperation"),
        ("verify_evidence", "check_source", "get_cooperation_timeline", "validate_relation", "check_constraints"),
        min_year_span=2, min_achievements=3,
    ),
    "RESEARCH_PARTNERSHIP": VerificationPolicy(
        "RESEARCH_PARTNERSHIP", "RESEARCH_PARTNER",
        ("get_common_papers", "get_common_projects", "get_common_patents", "aggregate_cooperation"),
        ("verify_evidence", "check_source", "validate_relation"),
    ),
    "ENTERPRISE_RELATION": VerificationPolicy(
        "ENTERPRISE_RELATION", "ENTERPRISE_RELATED",
        ("get_person_company_roles", "get_company_projects", "get_company_patents"),
        ("verify_evidence", "check_source"),
    ),
    "INDUSTRY_ASSOCIATION": VerificationPolicy(
        "INDUSTRY_ASSOCIATION", "INDUSTRY_ASSOCIATED",
        ("search_industry_segments", "get_chain_structure", "get_node_companies", "get_node_events", "rank_top_events"),
        ("verify_evidence", "check_source"),
    ),
    "GRAPH_PATH_CLAIM": VerificationPolicy(
        "GRAPH_PATH_CLAIM", "GRAPH_CONNECTED",
        ("get_neighbors", "get_neighbors_filtered", "find_path", "find_paths", "query_subgraph", "calculate_path_strength"),
        ("verify_evidence", "check_source"),
    ),
    "WEB_FACT_CLAIM": VerificationPolicy(
        "WEB_FACT_CLAIM", "PUBLIC_SOURCE_SUPPORTED",
        ("search_web",),
        ("verify_evidence", "check_source"),
    ),
}


def infer_claim_type(question: str) -> str:
    if any(word in question for word in ("长期稳定", "核心科研合作伙伴")):
        return "CORE_RESEARCH_PARTNER"
    if any(word in question for word in ("企业关联", "企业关系", "公司关系")):
        return "ENTERPRISE_RELATION"
    if any(word in question for word in ("产业归属", "产业关联")):
        return "INDUSTRY_ASSOCIATION"
    if any(word in question for word in ("路径是否", "是否连通", "多跳关系是否")):
        return "GRAPH_PATH_CLAIM"
    if any(word in question for word in ("网页事实", "公开来源是否", "新闻是否可信")):
        return "WEB_FACT_CLAIM"
    return "RESEARCH_PARTNERSHIP"


def get_verification_policy(claim_type: str | None, question: str) -> VerificationPolicy:
    resolved = claim_type or infer_claim_type(question)
    try:
        return POLICIES[resolved]
    except KeyError as exc:
        raise ValueError(f"未知验证结论类型: {resolved}") from exc
