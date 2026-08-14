"""科研成果 Agent 独享工具。"""
from langchain_core.tools import tool
from data.mock_achievements import PAPERS, PROJECTS


@tool
def get_author_papers(entity_id: str) -> list[dict]:
    """查询专家发表的论文。"""
    return [p.copy() for p in PAPERS if entity_id in p["authors"]]


@tool
def get_common_papers(entity_ids: list[str]) -> list[dict]:
    """查询两位或多位专家共同署名论文。"""
    wanted = set(entity_ids)
    return [p.copy() for p in PAPERS if wanted.issubset(set(p["authors"]))]


@tool
def aggregate_cooperation(entity_ids: list[str]) -> dict:
    """汇总共同论文数量与合作年份。"""
    papers = get_common_papers.invoke({"entity_ids": entity_ids})
    return {"entity_ids": entity_ids, "common_paper_count": len(papers), "years": sorted({p["year"] for p in papers}), "paper_ids": [p["paper_id"] for p in papers]}


@tool
def get_common_projects(entity_ids: list[str]) -> list[dict]:
    """查询两位或多位专家共同参与的科研项目。"""
    wanted = set(entity_ids)
    return [row.copy() for row in PROJECTS if wanted.issubset(set(row["participant_ids"]))]
