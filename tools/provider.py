"""Agent Tool 白名单与 local/mcp 双传输选择。"""
from __future__ import annotations

from typing import Any

from mcp_runtime.client import build_langchain_mcp_tools
from models.settings import Settings
from tools.achievement_tools import (
    aggregate_cooperation,
    get_author_papers,
    get_common_papers,
    get_common_patents,
    get_common_projects,
    get_person_patents,
)
from tools.enterprise_tools import (
    get_company_patents,
    get_company_projects,
    get_person_company_roles,
)
from tools.graph_tools import (
    calculate_path_strength,
    find_path,
    get_neighbors,
    k_hop_expand,
)
from tools.industry_tools import (
    get_chain_structure,
    get_node_companies,
    get_node_events,
    rank_top_events,
    search_industry_segments,
)
from tools.talent_tools import (
    get_education_history,
    get_employment_history,
    get_person_profile,
    match_employment_overlap,
)
from tools.verification_tools import (
    check_constraints,
    check_source,
    get_cooperation_timeline,
    validate_relation,
    verify_evidence,
)
from tools.web_search_tools import search_web

LOCAL_TOOL_GROUPS: dict[str, tuple[Any, ...]] = {
    "talent": (get_person_profile, get_employment_history, get_education_history, match_employment_overlap),
    "achievement": (get_author_papers, get_common_papers, aggregate_cooperation, get_common_projects,
                    get_person_patents, get_common_patents),
    "enterprise": (get_person_company_roles, get_company_projects, get_company_patents),
    "industry": (search_industry_segments, get_chain_structure, get_node_companies, get_node_events, rank_top_events),
    "graph": (get_neighbors, find_path, k_hop_expand, calculate_path_strength),
    "verification": (verify_evidence, check_source, validate_relation, check_constraints, get_cooperation_timeline),
    "web": (search_web,),
}


def tool_names(group: str) -> list[str]:
    try:
        return [item.name for item in LOCAL_TOOL_GROUPS[group]]
    except KeyError as exc:
        raise ValueError(f"未知工具组: {group}") from exc


def get_tools(group: str, settings: Settings | None = None, mcp_target: Any | None = None,
              use_discovery_cache: bool = True) -> list[Any]:
    settings = settings or Settings.from_env()
    local = list(LOCAL_TOOL_GROUPS.get(group, ()))
    if not local:
        raise ValueError(f"未知工具组: {group}")
    if settings.tool_transport == "local":
        return local
    if settings.tool_transport != "mcp":
        raise ValueError("TOOL_TRANSPORT 只能是 local 或 mcp")
    target = mcp_target if mcp_target is not None else settings.mcp_server_url
    return build_langchain_mcp_tools(target, [item.name for item in local], settings.mcp_request_timeout,
                                     use_discovery_cache=use_discovery_cache)
