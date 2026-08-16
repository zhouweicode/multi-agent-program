"""科研成果 Agent 独享工具。"""
from langchain_core.tools import tool
from services.resources import get_achievement_service


@tool
def get_author_papers(entity_id: str) -> list[dict]:
    """查询专家发表的论文。"""
    return get_achievement_service().get_author_papers(entity_id)


@tool
def get_common_papers(entity_ids: list[str]) -> list[dict]:
    """查询两位或多位专家共同署名论文。"""
    return get_achievement_service().get_common_papers(entity_ids)


@tool
def aggregate_cooperation(entity_ids: list[str]) -> dict:
    """汇总共同论文数量与合作年份。"""
    papers = get_common_papers.invoke({"entity_ids": entity_ids})
    return {"entity_ids": entity_ids, "common_paper_count": len(papers), "years": sorted({p["year"] for p in papers}), "paper_ids": [p["paper_id"] for p in papers]}


@tool
def get_common_projects(entity_ids: list[str]) -> list[dict]:
    """查询两位或多位专家共同参与的科研项目。"""
    return get_achievement_service().get_common_projects(entity_ids)
