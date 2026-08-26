"""科技知识图谱 MCP Server：复用现有 Tool/Service，不承载 LangGraph 编排逻辑。"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

from mcp.server import MCPServer

from models.settings import Settings
from services.resources import active_release_settings, close_resources
from tools.achievement_tools import aggregate_cooperation as local_aggregate_cooperation
from tools.achievement_tools import get_author_papers as local_get_author_papers
from tools.achievement_tools import get_common_papers as local_get_common_papers
from tools.achievement_tools import get_common_patents as local_get_common_patents
from tools.achievement_tools import get_common_projects as local_get_common_projects
from tools.achievement_tools import get_person_patents as local_get_person_patents
from tools.enterprise_tools import get_company_patents as local_get_company_patents
from tools.enterprise_tools import get_company_projects as local_get_company_projects
from tools.enterprise_tools import (
    get_person_company_roles as local_get_person_company_roles,
)
from tools.graph_tools import calculate_path_strength as local_calculate_path_strength
from tools.graph_tools import find_path as local_find_path
from tools.graph_tools import get_neighbors as local_get_neighbors
from tools.graph_tools import k_hop_expand as local_k_hop_expand
from tools.industry_tools import get_chain_structure as local_get_chain_structure
from tools.industry_tools import get_node_companies as local_get_node_companies
from tools.industry_tools import get_node_events as local_get_node_events
from tools.industry_tools import rank_top_events as local_rank_top_events
from tools.industry_tools import (
    search_industry_segments as local_search_industry_segments,
)
from tools.talent_tools import get_education_history as local_get_education_history
from tools.talent_tools import get_employment_history as local_get_employment_history
from tools.talent_tools import get_person_profile as local_get_person_profile
from tools.talent_tools import (
    match_employment_overlap as local_match_employment_overlap,
)
from tools.verification_tools import check_constraints as local_check_constraints
from tools.verification_tools import check_source as local_check_source
from tools.verification_tools import (
    get_cooperation_timeline as local_get_cooperation_timeline,
)
from tools.verification_tools import validate_relation as local_validate_relation
from tools.verification_tools import verify_evidence as local_verify_evidence
from tools.web_search_tools import search_web as local_search_web


@asynccontextmanager
async def _lifespan(_server: MCPServer):
    yield
    close_resources()


mcp = MCPServer(
    name="tech-kg-tools",
    title="科技知识图谱工具服务",
    description="向 Agent 标准化暴露科研数据查询、图谱检索、证据验证与联网搜索能力。",
    instructions="所有查询结果都是证据候选；联网摘要必须经过来源核验后才能作为最终事实。",
    version="1.0.0",
    lifespan=_lifespan,
)


def _invoke(tool, **arguments):
    return tool.invoke(arguments)


# Talent tools
@mcp.tool(name="get_person_profile", meta={"domain": "talent"})
def get_person_profile(entity_id: str) -> dict:
    """按唯一实体 ID 查询专家画像。"""
    return _invoke(local_get_person_profile, entity_id=entity_id)


@mcp.tool(name="get_employment_history", meta={"domain": "talent"})
def get_employment_history(entity_id: str) -> list[dict]:
    """查询专家任职经历。"""
    return _invoke(local_get_employment_history, entity_id=entity_id)


@mcp.tool(name="get_education_history", meta={"domain": "talent"})
def get_education_history(entity_id: str) -> list[dict]:
    """查询专家教育经历。"""
    return _invoke(local_get_education_history, entity_id=entity_id)


@mcp.tool(name="match_employment_overlap", meta={"domain": "talent"})
def match_employment_overlap(entity_ids: list[str]) -> list[dict]:
    """计算两位专家任职机构与时间是否重叠。"""
    return _invoke(local_match_employment_overlap, entity_ids=entity_ids)


# Achievement tools
@mcp.tool(name="get_author_papers", meta={"domain": "achievement"})
def get_author_papers(entity_id: str) -> list[dict]:
    """查询专家发表的论文。"""
    return _invoke(local_get_author_papers, entity_id=entity_id)


@mcp.tool(name="get_common_papers", meta={"domain": "achievement"})
def get_common_papers(entity_ids: list[str]) -> list[dict]:
    """查询两位或多位专家共同署名论文。"""
    return _invoke(local_get_common_papers, entity_ids=entity_ids)


@mcp.tool(name="aggregate_cooperation", meta={"domain": "achievement"})
def aggregate_cooperation(entity_ids: list[str]) -> dict:
    """汇总共同论文数量与合作年份。"""
    return _invoke(local_aggregate_cooperation, entity_ids=entity_ids)


@mcp.tool(name="get_common_projects", meta={"domain": "achievement"})
def get_common_projects(entity_ids: list[str]) -> list[dict]:
    """查询两位或多位专家共同参与的科研项目。"""
    return _invoke(local_get_common_projects, entity_ids=entity_ids)


@mcp.tool(name="get_person_patents", meta={"domain": "achievement"})
def get_person_patents(entity_id: str) -> list[dict]:
    """查询专家作为发明人的专利。"""
    return _invoke(local_get_person_patents, entity_id=entity_id)


@mcp.tool(name="get_common_patents", meta={"domain": "achievement"})
def get_common_patents(entity_ids: list[str]) -> list[dict]:
    """查询两位或多位专家共同发明的专利。"""
    return _invoke(local_get_common_patents, entity_ids=entity_ids)


# Enterprise tools
@mcp.tool(name="get_person_company_roles", meta={"domain": "enterprise"})
def get_person_company_roles(entity_ids: list[str]) -> list[dict]:
    """查询专家在企业中的任职、顾问等角色。"""
    return _invoke(local_get_person_company_roles, entity_ids=entity_ids)


@mcp.tool(name="get_company_projects", meta={"domain": "enterprise"})
def get_company_projects(company_id: str) -> list[dict]:
    """查询企业参与的联合项目。"""
    return _invoke(local_get_company_projects, company_id=company_id)


@mcp.tool(name="get_company_patents", meta={"domain": "enterprise"})
def get_company_patents(company_id: str) -> list[dict]:
    """查询企业拥有或参与的专利。"""
    return _invoke(local_get_company_patents, company_id=company_id)


# Industry tools
@mcp.tool(name="search_industry_segments", meta={"domain": "industry"})
def search_industry_segments(query: str = "", limit: int = 10) -> list[dict]:
    """按产业名称检索真实 segment_id；query 为空时返回事件最多的节点。"""
    return _invoke(local_search_industry_segments, query=query, limit=limit)


@mcp.tool(name="get_chain_structure", meta={"domain": "industry"})
def get_chain_structure(chain_id: str) -> dict:
    """查询产业链及其上中下游节点结构。"""
    return _invoke(local_get_chain_structure, chain_id=chain_id)


@mcp.tool(name="get_node_companies", meta={"domain": "industry"})
def get_node_companies(node_id: str) -> list[dict]:
    """查询产业节点关联企业。"""
    return _invoke(local_get_node_companies, node_id=node_id)


@mcp.tool(name="get_node_events", meta={"domain": "industry"})
def get_node_events(node_id: str) -> list[dict]:
    """查询产业节点事件。"""
    return _invoke(local_get_node_events, node_id=node_id)


@mcp.tool(name="rank_top_events", meta={"domain": "industry"})
def rank_top_events(node_id: str, top_n: int = 3) -> list[dict]:
    """按重要度返回产业节点 TOP-N 事件。"""
    return _invoke(local_rank_top_events, node_id=node_id, top_n=top_n)


# Graph tools
@mcp.tool(name="get_neighbors", meta={"domain": "graph"})
def get_neighbors(entity_id: str) -> list[dict]:
    """查询实体的一跳邻居及关系。"""
    return _invoke(local_get_neighbors, entity_id=entity_id)


@mcp.tool(name="find_path", meta={"domain": "graph"})
def find_path(source_id: str, target_id: str, max_hops: int = 4) -> dict:
    """查找两个实体间的一条最短关系路径。"""
    return _invoke(local_find_path, source_id=source_id, target_id=target_id, max_hops=max_hops)


@mcp.tool(name="k_hop_expand", meta={"domain": "graph"})
def k_hop_expand(entity_id: str, k: int = 2) -> dict:
    """从实体出发扩展 K 跳，返回每一层新发现的实体。"""
    return _invoke(local_k_hop_expand, entity_id=entity_id, k=k)


@mcp.tool(name="calculate_path_strength", meta={"domain": "graph"})
def calculate_path_strength(source_id: str, target_id: str) -> dict:
    """按路径边权乘积计算关系强度。"""
    return _invoke(local_calculate_path_strength, source_id=source_id, target_id=target_id)


# Verification tools
@mcp.tool(name="verify_evidence", meta={"domain": "verification"})
def verify_evidence(evidence_ids: list[str], entity_ids: list[str]) -> dict:
    """验证证据 ID 是否全部存在，并返回缺失项。"""
    return _invoke(local_verify_evidence, evidence_ids=evidence_ids, entity_ids=entity_ids)


@mcp.tool(name="check_source", meta={"domain": "verification"})
def check_source(evidence_ids: list[str], entity_ids: list[str]) -> dict:
    """检查证据是否来自当前受信任的数据后端。"""
    return _invoke(local_check_source, evidence_ids=evidence_ids, entity_ids=entity_ids)


@mcp.tool(name="validate_relation", meta={"domain": "verification"})
def validate_relation(entity_ids: list[str], relation: str) -> dict:
    """检查科研合作关系是否同时包含共同论文和共同项目支持。"""
    return _invoke(local_validate_relation, entity_ids=entity_ids, relation=relation)


@mcp.tool(name="check_constraints", meta={"domain": "verification"})
def check_constraints(timeline: list[dict], min_year_span: int, min_achievements: int) -> dict:
    """检查长期稳定合作所需的年份跨度与成果数量约束。"""
    return _invoke(local_check_constraints, timeline=timeline, min_year_span=min_year_span,
                   min_achievements=min_achievements)


@mcp.tool(name="get_cooperation_timeline", meta={"domain": "verification"})
def get_cooperation_timeline(entity_ids: list[str]) -> list[dict]:
    """按时间生成两位专家共同论文和项目的合作时间线。"""
    return _invoke(local_get_cooperation_timeline, entity_ids=entity_ids)


# Open-world research tool. It is exposed through MCP but is not granted to existing domain Agents.
@mcp.tool(name="search_web", meta={"domain": "web", "open_world": True})
def search_web(query: str, max_results: int = 5, recency_days: int | None = None,
               domains: list[str] | None = None) -> dict:
    """联网搜索公开网页；结果是待验证的外部证据候选，不得直接覆盖图谱事实。"""
    return _invoke(local_search_web, query=query, max_results=max_results,
                   recency_days=recency_days, domains=domains)


@mcp.resource("kg://ontology/domains", name="knowledge_graph_domains", mime_type="application/json")
def domain_ontology() -> str:
    """Agent、领域与工具命名空间的只读说明。"""
    payload = {
        "talent": ["get_person_profile", "get_employment_history", "get_education_history", "match_employment_overlap"],
        "achievement": ["get_author_papers", "get_common_papers", "aggregate_cooperation", "get_common_projects",
                        "get_person_patents", "get_common_patents"],
        "enterprise": ["get_person_company_roles", "get_company_projects", "get_company_patents"],
        "industry": ["search_industry_segments", "get_chain_structure", "get_node_companies", "get_node_events",
                     "rank_top_events"],
        "graph": ["get_neighbors", "find_path", "k_hop_expand", "calculate_path_strength"],
        "verification": ["verify_evidence", "check_source", "validate_relation", "check_constraints",
                         "get_cooperation_timeline"],
        "web": ["search_web"],
    }
    return json.dumps(payload, ensure_ascii=False)


@mcp.resource("kg://runtime/active-release", name="active_kg_release", mime_type="application/json")
def active_kg_release() -> str:
    """当前被原子激活的图谱发布版本。"""
    settings, release = active_release_settings()
    return json.dumps(release or {"release_id": None, "milvus_collection": settings.milvus_collection},
                      ensure_ascii=False, default=str)


@mcp.prompt(name="research_query")
def research_query(question: str) -> str:
    """生成一条强调证据约束的科研图谱查询提示。"""
    return ("请仅使用已授权的知识图谱工具回答下列问题。区分图谱内部事实与联网证据候选，"
            "对冲突信息说明来源差异，不得使用未经工具返回的模型记忆：\n" + question)


def main() -> None:
    settings = Settings.from_env()
    path = settings.mcp_server_path if settings.mcp_server_path.startswith("/") else f"/{settings.mcp_server_path}"
    mcp.run("streamable-http", host=settings.mcp_server_host, port=settings.mcp_server_port,
            streamable_http_path=path, json_response=True, stateless_http=True)


if __name__ == "__main__":
    main()
