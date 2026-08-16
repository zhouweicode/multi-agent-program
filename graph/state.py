"""全图共享状态。Node 只读写自己需要的字段。"""
from typing import Any, TypedDict


class GraphRAGState(TypedDict, total=False):
    thread_id: str
    question: str
    intent: str
    complexity: str
    primary_domain: str
    requires_verification: bool
    entity_mentions: list[str]
    resolved_entities: dict[str, str]
    entity_backend_ids: dict[str, dict[str, str]]
    entity_candidates: dict[str, list[dict[str, Any]]]
    awaiting_user_selection: bool
    entity_resolution_status: str
    unresolved_mentions: list[str]
    plan: dict[str, Any]
    tasks: list[dict[str, Any]]
    task_history: list[dict[str, Any]]
    talent_result: dict[str, Any]
    achievement_result: dict[str, Any]
    enterprise_result: dict[str, Any]
    industry_result: dict[str, Any]
    graph_result: dict[str, Any]
    evidence: list[dict[str, Any]]
    validation_result: dict[str, Any]
    verification_result: dict[str, Any]
    replan_count: int
    max_replans: int
    final_answer: str
