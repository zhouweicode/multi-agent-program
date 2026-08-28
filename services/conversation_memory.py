"""Conversation-scoped entity focus, reference resolution and writeback."""
from __future__ import annotations

import re
from typing import Any

from langgraph.types import interrupt

from graph.state import GraphRAGState
from models.settings import Settings
from services.long_term_memory import build_memory_update_payload
from services.memory_manager import memory_manager
from services.memory_recall import recall_long_term_memory
from services.observability import emit_event
from services.resources import get_entity_service
from services.telemetry import traced_span

_REFERENCE_PATTERNS = (
    r"这位教授", r"该教授", r"这位学者", r"该学者", r"这位专家", r"该专家",
    r"(?<!其)他(?!们)", r"她(?!们)",
)
_REFERENCE_RE = re.compile("|".join(f"(?:{pattern})" for pattern in _REFERENCE_PATTERNS))


def _references(question: str) -> list[str]:
    return list(dict.fromkeys(match.group(0) for match in _REFERENCE_RE.finditer(question)))


def _candidate(entity: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_id": entity["entity_id"],
        "name": entity["name"],
        "organization": entity.get("organization"),
        "title": entity.get("title"),
        "entity_type": entity.get("entity_type", "scholar"),
        "match_reasons": ["当前会话中最近确认的实体"],
    }


def recall_conversation_memory(state: GraphRAGState) -> dict[str, Any]:
    original = state.get("original_question") or state["question"]
    long_term = recall_long_term_memory(dict(state))
    user_id = state.get("user_id")
    if not state.get("memory_enabled") or not state.get("conversation_id") or not user_id:
        return long_term | {"original_question": original, "contextualized_question": original,
                "conversation_entities": [], "memory_reference_resolution": {},
                "memory_status": "DISABLED"}

    conversation_id = state["conversation_id"]
    run_id = state.get("thread_id")
    with traced_span("memory.conversation.recall", "memory", {
        "run.id": run_id, "conversation.id": conversation_id,
    }):
        memory = memory_manager().recall_context(
            user_id, conversation_id, query=original, top_k=0
        )["conversation"]
        entities = memory["entities"]
        references = _references(original)
        if not references:
            emit_event("MEMORY_RECALLED", thread_id=run_id, conversation_id=conversation_id,
                       entity_count=len(entities), reference_count=0, status="NO_REFERENCE")
            return long_term | {"original_question": original, "contextualized_question": original,
                    "conversation_entities": entities, "memory_reference_resolution": {},
                    "memory_status": "NO_REFERENCE"}
        if not entities:
            emit_event("MEMORY_RECALLED", thread_id=run_id, conversation_id=conversation_id,
                       entity_count=0, reference_count=len(references), status="EMPTY")
            return long_term | {"original_question": original, "contextualized_question": original,
                    "conversation_entities": [], "memory_reference_resolution": {},
                    "memory_status": "EMPTY"}

        selected = entities[0] if len(entities) == 1 else None
        if selected is None:
            reference_key = references[0]
            emit_event("MEMORY_REFERENCE_AMBIGUOUS", thread_id=run_id,
                       conversation_id=conversation_id, reference=reference_key,
                       candidate_count=len(entities))
            selections = interrupt({
                "status": "NEED_USER_SELECTION",
                "reason": "MEMORY_REFERENCE_AMBIGUOUS",
                "candidates": {reference_key: [_candidate(row) for row in entities]},
                "instruction": f"请确认“{reference_key}”指代的专家",
            })
            if not isinstance(selections, dict) or reference_key not in selections:
                raise ValueError(f"请为“{reference_key}”选择一个会话实体")
            selected = next((row for row in entities if row["entity_id"] == selections[reference_key]), None)
            if selected is None:
                raise ValueError(f"“{reference_key}”的会话实体选择无效")

        contextualized = _REFERENCE_RE.sub(selected["name"], original)
        resolution = {reference: {"name": selected["name"], "entity_id": selected["entity_id"]}
                      for reference in references}
        emit_event("MEMORY_REFERENCE_RESOLVED", thread_id=run_id, conversation_id=conversation_id,
                   references=references, entity_id=selected["entity_id"], entity_name=selected["name"])
        return long_term | {
            "question": contextualized,
            "original_question": original,
            "contextualized_question": contextualized,
            "conversation_entities": entities,
            "memory_reference_resolution": resolution,
            "memory_status": "REFERENCE_RESOLVED",
            # A user-confirmed entity ID is safer than resolving the pronoun's
            # replacement name again, especially for duplicate scholar names.
            "resolved_entities": {selected["name"]: selected["entity_id"]},
        }


def write_conversation_memory(state: GraphRAGState) -> dict[str, Any]:
    user_id = state.get("user_id")
    if not state.get("memory_enabled") or not state.get("conversation_id") or not user_id:
        return {"memory_status": "DISABLED"}
    if not state.get("final_answer"):
        return {"memory_status": "SKIPPED"}

    run_id = state.get("thread_id", "")
    conversation_id = state["conversation_id"]
    entities = []
    entity_service = get_entity_service()
    for name, entity_id in state.get("resolved_entities", {}).items():
        known = next((row for row in state.get("conversation_entities", [])
                      if row.get("entity_id") == entity_id), None)
        details = known or entity_service.get(entity_id) or {}
        entities.append({
            "entity_id": entity_id,
            "name": name,
            "organization": details.get("organization"),
            "title": details.get("title"),
            "entity_type": "scholar",
        })

    with traced_span("memory.conversation.writeback", "memory", {
        "run.id": run_id, "conversation.id": conversation_id,
        "memory.entity_count": len(entities),
    }):
        manager = memory_manager()
        memory = manager.record_turn(
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
            original_question=state.get("original_question") or state.get("question", ""),
            contextualized_question=state.get("contextualized_question") or state.get("question", ""),
            final_answer=state.get("final_answer"),
            intent=state.get("intent"),
            primary_domain=state.get("primary_domain"),
            entities=entities,
        )
    extraction_status = "DISABLED"
    if Settings.from_env().memory_extraction_enabled:
        try:
            queued = manager.enqueue_update(
                user_id=user_id,
                run_id=run_id,
                payload=build_memory_update_payload(dict(state)),
                conversation_id=conversation_id,
            )
            extraction_status = "QUEUED" if queued else "DUPLICATE"
            emit_event(
                "LONG_TERM_MEMORY_UPDATE_QUEUED" if queued else
                "LONG_TERM_MEMORY_UPDATE_SKIPPED",
                thread_id=run_id,
                conversation_id=conversation_id,
                reason=None if queued else "duplicate_run",
            )
        except Exception as exc:  # noqa: BLE001 - extraction is fail-open
            extraction_status = "FAILED_OPEN"
            emit_event(
                "LONG_TERM_MEMORY_UPDATE_FAILED_OPEN",
                thread_id=run_id,
                conversation_id=conversation_id,
                error_type=type(exc).__name__,
            )
    emit_event("MEMORY_WRITTEN", thread_id=run_id, conversation_id=conversation_id,
               turn_count=memory["turn_count"], entity_count=len(memory["entities"]))
    return {"conversation_entities": memory["entities"],
            "conversation_turn_count": memory["turn_count"], "memory_status": "WRITTEN",
            "long_term_memory_update_status": extraction_status}
