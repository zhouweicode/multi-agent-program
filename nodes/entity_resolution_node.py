"""实体消歧 Node：唯一候选自动解析，多候选通过 interrupt 等待用户选择。"""
import logging
from langgraph.types import interrupt
from graph.state import GraphRAGState
from services.entity_service import EntityService
from services.observability import emit_event

logger = logging.getLogger(__name__)
service = EntityService()


def entity_resolution_node(state: GraphRAGState) -> dict:
    candidates, resolved, ambiguous = {}, dict(state.get("resolved_entities", {})), {}
    for mention in state.get("entity_mentions", []):
        if mention in resolved:
            continue
        rows = service.search(mention)
        candidates[mention] = rows
        if len(rows) == 1:
            resolved[mention] = rows[0]["entity_id"]
        elif len(rows) > 1:
            ambiguous[mention] = rows

    if ambiguous:
        logger.info("Entity Resolution: NEED_USER_SELECTION %s", list(ambiguous))
        emit_event("ENTITY_RESOLUTION_INTERRUPTED", thread_id=state.get("thread_id"), mentions=list(ambiguous))
        selections = interrupt({"status": "NEED_USER_SELECTION", "candidates": ambiguous,
                                "instruction": "请为每个姓名选择一个 entity_id"})
        if not isinstance(selections, dict):
            raise ValueError("恢复执行时必须传入 {姓名: entity_id}")
        for mention, entity_id in selections.items():
            valid_ids = {x["entity_id"] for x in ambiguous.get(mention, [])}
            if entity_id not in valid_ids:
                raise ValueError(f"{mention} 的 entity_id 无效: {entity_id}")
            resolved[mention] = entity_id

    logger.info("Entity Resolution: resolved=%s", resolved)
    emit_event("ENTITY_RESOLUTION_COMPLETED", thread_id=state.get("thread_id"), resolved_entities=resolved)
    backend_ids = {
        mention: service.mapping.backend_ids(entity_id)
        for mention, entity_id in resolved.items()
    }
    return {"resolved_entities": resolved, "entity_backend_ids": backend_ids,
            "entity_candidates": candidates, "awaiting_user_selection": False}
