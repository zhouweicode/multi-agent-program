"""专家报告的稳定输出协议。"""
from typing import Any, Literal

from pydantic import BaseModel, Field


class ReportClaim(BaseModel):
    text: str
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0, le=1)


class ReportSection(BaseModel):
    section_id: str
    title: str
    summary: str
    claims: list[ReportClaim] = Field(default_factory=list)
    status: Literal["complete", "partial", "unavailable"] = "complete"


class ExpertReport(BaseModel):
    schema_version: str = "1.0"
    skill_id: str = "expert_report"
    skill_version: str
    entity_id: str
    entity_name: str
    report_type: Literal["brief", "comprehensive"]
    audience: str
    executive_summary: str
    sections: list[ReportSection]
    strengths: list[ReportClaim] = Field(default_factory=list)
    risks_and_gaps: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_coverage: float = Field(ge=0, le=1)
    evidence_catalog: list[dict[str, Any]] = Field(default_factory=list)
