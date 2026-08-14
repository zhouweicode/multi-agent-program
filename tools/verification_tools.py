"""Verification Agent 独享的证据与关系验证工具。"""
from langchain_core.tools import tool
from data.mock_achievements import PAPERS, PROJECTS
from services.evidence_service import EvidenceService


@tool
def verify_evidence(evidence_ids: list[str]) -> dict:
    """验证证据 ID 是否全部存在，并返回缺失项。"""
    service = EvidenceService()
    missing = [item for item in evidence_ids if not service.exists(item)]
    return {"valid": not missing and bool(evidence_ids), "checked_count": len(evidence_ids), "missing": missing}


@tool
def check_source(evidence_ids: list[str]) -> dict:
    """检查证据是否来自当前受信任的 Mock 科研数据源。"""
    trusted_prefixes = ("ev_paper_", "ev_project_")
    untrusted = [item for item in evidence_ids if not item.startswith(trusted_prefixes)]
    return {"trusted": not untrusted and bool(evidence_ids), "untrusted": untrusted, "source": "mock_knowledge_graph"}


@tool
def validate_relation(entity_ids: list[str], relation: str) -> dict:
    """检查科研合作关系是否同时包含共同论文和共同项目支持。"""
    wanted = set(entity_ids)
    papers = [row for row in PAPERS if wanted.issubset(set(row["authors"]))]
    projects = [row for row in PROJECTS if wanted.issubset(set(row["participant_ids"]))]
    return {"relation": relation, "supported": bool(papers and projects), "common_paper_count": len(papers),
            "common_project_count": len(projects), "evidence_ids": [x["evidence_id"] for x in papers + projects]}


@tool
def check_constraints(timeline: list[dict], min_year_span: int, min_achievements: int) -> dict:
    """检查长期稳定合作所需的年份跨度与成果数量约束。"""
    years = sorted({row["year"] for row in timeline})
    span = years[-1] - years[0] if len(years) >= 2 else 0
    return {"satisfied": span >= min_year_span and len(timeline) >= min_achievements,
            "year_span": span, "achievement_count": len(timeline),
            "constraints": {"min_year_span": min_year_span, "min_achievements": min_achievements}}


@tool
def get_cooperation_timeline(entity_ids: list[str]) -> list[dict]:
    """按时间生成两位专家共同论文和项目的合作时间线。"""
    wanted = set(entity_ids)
    rows = [{"year": p["year"], "type": "paper", "id": p["paper_id"], "evidence_id": p["evidence_id"]}
            for p in PAPERS if wanted.issubset(set(p["authors"]))]
    rows.extend({"year": p["start_year"], "type": "project", "id": p["project_id"], "evidence_id": p["evidence_id"]}
                for p in PROJECTS if wanted.issubset(set(p["participant_ids"])))
    return sorted(rows, key=lambda row: row["year"])
