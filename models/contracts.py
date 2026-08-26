"""Supervisor 任务验收契约；描述业务事实类型，不把 Tool 名写进 Planner。"""

AGENT_DOMAINS = {
    "talent_agent": "talent",
    "achievement_agent": "achievement",
    "enterprise_agent": "enterprise",
    "industry_agent": "industry",
    "graph_reasoning_agent": "graph",
    "web_research_agent": "web",
}

FACT_TYPE_TO_TOOL = {
    "employment_overlap": "match_employment_overlap",
    "common_papers": "get_common_papers",
    "common_projects": "get_common_projects",
    "cooperation_summary": "aggregate_cooperation",
    "common_patents": "get_common_patents",
    "company_roles": "get_person_company_roles",
    "company_projects": "get_company_projects",
    "company_patents": "get_company_patents",
    "chain_structure": "get_chain_structure",
    "node_companies": "get_node_companies",
    "node_events": "get_node_events",
    "ranked_events": "rank_top_events",
    "neighbors": "get_neighbors",
    "path": "find_path",
    "k_hop_subgraph": "k_hop_expand",
    "path_strength": "calculate_path_strength",
    "web_sources": "search_web",
}


def required_fact_types(agent: str, question: str) -> list[str]:
    """根据明确任务语义细化静态领域契约；专利查询不强制返回论文和项目。"""
    if agent == "achievement_agent" and "专利" in question and not any(word in question for word in ("论文", "学术", "科研项目")):
        return ["common_patents"]
    return list(DEFAULT_REQUIRED_FACT_TYPES[agent])

DEFAULT_REQUIRED_FACT_TYPES = {
    "talent_agent": ["employment_overlap"],
    "achievement_agent": ["common_papers", "common_projects", "cooperation_summary"],
    "enterprise_agent": ["company_roles", "company_projects", "company_patents"],
    "industry_agent": ["chain_structure", "node_companies", "node_events", "ranked_events"],
    "graph_reasoning_agent": ["neighbors", "path", "path_strength"],
    "web_research_agent": ["web_sources"],
}
