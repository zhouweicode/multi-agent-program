"""Conversation-scoped entity focus, reference resolution and writeback."""
from __future__ import annotations

import re
from threading import Lock
from typing import Any

from langgraph.types import interrupt

from graph.state import GraphRAGState
from models.settings import Settings
from repositories.conversation_memory_repository import SQLiteConversationMemoryRepository
from services.observability import emit_event
from services.resources import get_entity_service
from services.telemetry import traced_span

_REFERENCE_PATTERNS = (
    r"这位教授", r"该教授", r"这位学者", r"该学者", r"这位专家", r"该专家",
    r"(?<!其)他(?!们)", r"她(?!们)",
)
_REFERENCE_RE = re.compile("|".join(f"(?:{pattern})" for pattern in _REFERENCE_PATTERNS))


_repositories: dict[str, SQLiteConversationMemoryRepository] = {}
_repository_lock = Lock()


def conversation_memory_repository() -> SQLiteConversationMemoryRepository:
    path = Settings.from_env().conversation_memory_db_path
    with _repository_lock:
        repository = _repositories.get(path)
        if repository is None:
            repository = SQLiteConversationMemoryRepository(path)
            _repositories[path] = repository
        return repository


def close_conversation_memory() -> None:
    with _repository_lock:
        repositories = list(_repositories.values())
        _repositories.clear()
    for repository in repositories:
        repository.close()


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
    if not state.get("memory_enabled") or not state.get("conversation_id"):
        return {"original_question": original, "contextualized_question": original,
                "conversation_entities": [], "memory_reference_resolution": {},
                "memory_status": "DISABLED"}

    conversation_id = state["conversation_id"]
    run_id = state.get("thread_id")
    with traced_span("memory.conversation.recall", "memory", {
        "run.id": run_id, "conversation.id": conversation_id,
    }):
        memory = conversation_memory_repository().get(conversation_id)
        entities = memory["entities"]
        references = _references(original)
        if not references:
            emit_event("MEMORY_RECALLED", thread_id=run_id, conversation_id=conversation_id,
                       entity_count=len(entities), reference_count=0, status="NO_REFERENCE")
            return {"original_question": original, "contextualized_question": original,
                    "conversation_entities": entities, "memory_reference_resolution": {},
                    "memory_status": "NO_REFERENCE"}
        if not entities:
            emit_event("MEMORY_RECALLED", thread_id=run_id, conversation_id=conversation_id,
                       entity_count=0, reference_count=len(references), status="EMPTY")
            return {"original_question": original, "contextualized_question": original,
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
        return {
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
    if not state.get("memory_enabled") or not state.get("conversation_id"):
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
        memory = conversation_memory_repository().record_turn(
            conversation_id=conversation_id,
            run_id=run_id,
            original_question=state.get("original_question") or state.get("question", ""),
            contextualized_question=state.get("contextualized_question") or state.get("question", ""),
            final_answer=state.get("final_answer"),
            intent=state.get("intent"),
            primary_domain=state.get("primary_domain"),
            entities=entities,
        )
    emit_event("MEMORY_WRITTEN", thread_id=run_id, conversation_id=conversation_id,
               turn_count=memory["turn_count"], entity_count=len(memory["entities"]))
    return {"conversation_entities": memory["entities"],
            "conversation_turn_count": memory["turn_count"], "memory_status": "WRITTEN"}
