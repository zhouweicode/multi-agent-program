"""Router、Planner、Agent 与 Validator 的结构化输出协议。"""
from typing import Any, Literal

from pydantic import BaseModel, Field


class RouterOutput(BaseModel):
    intent: str = Field(description="用户意图")
    entity_mentions: list[str]
    complexity: Literal["simple", "complex"]
    primary_domain: Literal["talent", "achievement", "enterprise", "industry", "graph", "web"]
    requires_verification: bool = False


class EntityCandidate(BaseModel):
    entity_id: str
    name: str
    organization: str
    title: str


class PlannedTask(BaseModel):
    task_id: str
    agent: Literal["talent_agent", "achievement_agent", "enterprise_agent", "industry_agent", "graph_reasoning_agent", "web_research_agent"]
    goal: str
    required_fact_types: list[str] = Field(default_factory=list, description="完成任务必须返回的业务事实类型")
    required_entity_ids: list[str] = Field(default_factory=list, description="任务涉及的规范实体 ID")
    depends_on: list[str] = Field(default_factory=list, description="必须先完成的任务 ID")


class SupervisorPlan(BaseModel):
    tasks: list[PlannedTask]
    execution_mode: Literal["parallel", "sequential"] = "parallel"
    reason: str


class ToolCallSpec(BaseModel):
    name: str
    arguments: dict[str, Any]


class DomainResult(BaseModel):
    agent: str
    summary: str
    response: str | None = Field(default=None, description="Agent 基于 Tool Observation 形成的最终回答")
    facts: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[ToolCallSpec] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    stop_reason: str = "completed"


class EvidenceRecord(BaseModel):
    """跨 MySQL、Neo4j 与 Mock 后端统一的证据协议。"""
    evidence_id: str
    fact_type: str
    source_type: Literal["mysql", "neo4j", "milvus", "web", "mock", "derived", "unknown"] = "unknown"
    source_name: str
    source_record_id: str
    entity_ids: list[str] = Field(default_factory=list)
    event_time: int | str | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    source_tool: str


class ValidationResult(BaseModel):
    valid: bool
    needs_replan: bool = False
    missing_domains: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    status: Literal["PASS", "FAIL"]
    relation: str
    confidence: float = Field(ge=0, le=1)
    reason: str
    needs_replan: bool = False
    missing_evidence: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallSpec] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
