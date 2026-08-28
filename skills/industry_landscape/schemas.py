"""产业全景报告输出协议。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class IndustryLandscapeInput(BaseModel):
    report_type: Literal["brief", "comprehensive"] = "comprehensive"
    audience: str = "internal"
    industry_query: str = ""
    include_web: bool = False
    top_n_companies: int = Field(default=10, ge=1, le=50)
    top_n_events: int = Field(default=10, ge=1, le=50)


class IndustryClaim(BaseModel):
    text: str
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0, le=1)


class IndustrySection(BaseModel):
    section_id: str
    title: str
    summary: str
    claims: list[IndustryClaim] = Field(default_factory=list)
    status: Literal["complete", "partial", "unavailable"] = "complete"


class IndustryLandscapeReport(BaseModel):
    schema_version: str = "1.0"
    skill_id: str = "industry_landscape"
    skill_version: str
    industry_id: str
    industry_name: str
    report_type: Literal["brief", "comprehensive"]
    audience: str
    executive_summary: str
    sections: list[IndustrySection]
    key_signals: list[IndustryClaim] = Field(default_factory=list)
    risks_and_gaps: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_coverage: float = Field(ge=0, le=1)
    evidence_catalog: list[dict[str, Any]] = Field(default_factory=list)
