"""Router、Planner、Agent 与 Validator 的结构化输出协议。"""
from typing import Any, Literal
from pydantic import BaseModel, Field


class RouterOutput(BaseModel):
    intent: str = Field(description="用户意图")
    entity_mentions: list[str]
    complexity: Literal["simple", "complex"]
    primary_domain: Literal["talent", "achievement", "enterprise", "industry", "graph"]
    requires_verification: bool = False


class EntityCandidate(BaseModel):
    entity_id: str
    name: str
    organization: str
    title: str


class PlannedTask(BaseModel):
    task_id: str
    agent: Literal["talent_agent", "achievement_agent", "enterprise_agent", "industry_agent", "graph_reasoning_agent"]
    goal: str


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
    facts: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[ToolCallSpec] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


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
