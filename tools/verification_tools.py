"""Verification Agent 独享的证据与关系验证工具。"""
from langchain_core.tools import tool
from services.evidence_service import EvidenceService


@tool
def verify_evidence(evidence_ids: list[str], entity_ids: list[str]) -> dict:
    """验证证据 ID 是否全部存在，并返回缺失项。"""
    return EvidenceService().verify(evidence_ids, entity_ids)


@tool
def check_source(evidence_ids: list[str], entity_ids: list[str]) -> dict:
    """通过统一证据服务检查证据是否来自当前受信任的数据后端。"""
    return EvidenceService().check_sources(evidence_ids, entity_ids)


@tool
def validate_relation(entity_ids: list[str], relation: str) -> dict:
    """检查科研合作关系是否同时包含共同论文和共同项目支持。"""
    return EvidenceService().relation(entity_ids, relation)


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
    return EvidenceService().timeline(entity_ids)
