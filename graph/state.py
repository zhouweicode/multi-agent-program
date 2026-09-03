"""全图共享状态。Node 只读写自己需要的字段。"""

import operator
from typing import Annotated, Any, TypedDict


def merge_task_results(
    left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Merge independently completed task instances by their generation key."""
    return {**(left or {}), **(right or {})}


class GraphRAGState(TypedDict, total=False):
    user_id: str
    thread_id: str
    question: str
    original_question: str
    contextualized_question: str
    conversation_id: str
    memory_enabled: bool
    memory_status: str
    long_term_memory_update_status: str
    long_term_memory_recall_status: str
    long_term_memory_facts: list[dict[str, Any]]
    long_term_memory_prompt: str
    long_term_memory_used_fact_ids: list[str]
    long_term_memory_applied_fact_ids: list[str]
    long_term_memory_estimated_tokens: int
    memory_reference_resolution: dict[str, dict[str, str]]
    conversation_entities: list[dict[str, Any]]
    conversation_turn_count: int
    experience_memory_enabled: bool
    experience_recall_status: str
    experience_writeback_status: str
    experience_global_writeback_status: str
    experience_mode: str
    experience_query_template: str
    experience_candidates: list[dict[str, Any]]
    experience_match: dict[str, Any] | None
    experience_strategy: dict[str, Any]
    experience_route_agreement: bool
    experience_pattern: dict[str, Any]
    web_search_enabled: bool
    requested_skill: str
    skill_version: str
    skill_content_hash: str
    skill_input: dict[str, Any]
    skill_capabilities: list[str]
    skill_required_domains: list[str]
    skill_instruction_digest: str
    intent: str
    complexity: str
    primary_domain: str
    requires_verification: bool
    verification_claim_type: str
    entity_mentions: list[str]
    resolved_entities: dict[str, str]
    entity_backend_ids: dict[str, dict[str, str]]
    entity_candidates: dict[str, list[dict[str, Any]]]
    awaiting_user_selection: bool
    entity_resolution_status: str
    unresolved_mentions: list[str]
    plan: dict[str, Any]
    tasks: list[dict[str, Any]]
    active_task: dict[str, Any]
    task_completions: Annotated[list[str], operator.add]
    task_results: Annotated[dict[str, dict[str, Any]], merge_task_results]
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
    report_draft: dict[str, Any]
    report_markdown: str
    replan_count: int
    max_replans: int
    final_answer: str
