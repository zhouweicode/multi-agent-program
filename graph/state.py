"""全图共享状态。Node 只读写自己需要的字段。"""
from typing import Any, TypedDict


class GraphRAGState(TypedDict, total=False):
    thread_id: str
    question: str
    original_question: str
    contextualized_question: str
    conversation_id: str
    memory_enabled: bool
    memory_status: str
    memory_reference_resolution: dict[str, dict[str, str]]
    conversation_entities: list[dict[str, Any]]
    conversation_turn_count: int
    experience_memory_enabled: bool
    experience_recall_status: str
    experience_writeback_status: str
    experience_mode: str
    experience_query_template: str
    experience_candidates: list[dict[str, Any]]
    experience_match: dict[str, Any] | None
    experience_strategy: dict[str, Any]
    experience_route_agreement: bool
    experience_pattern: dict[str, Any]
    web_search_enabled: bool
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
    web_result: dict[str, Any]
    evidence: list[dict[str, Any]]
    validation_result: dict[str, Any]
    verification_result: dict[str, Any]
    replan_count: int
    max_replans: int
    final_answer: str
