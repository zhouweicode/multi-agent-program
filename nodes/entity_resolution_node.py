"""实体消歧 Node：唯一候选自动解析，多候选通过 interrupt 等待用户选择。"""
import logging
from langgraph.types import interrupt
from graph.state import GraphRAGState
from services.resources import get_entity_service
from services.observability import emit_event

logger = logging.getLogger(__name__)
def entity_resolution_node(state: GraphRAGState) -> dict:
    service = get_entity_service()
    candidates, resolved, ambiguous, not_found = {}, dict(state.get("resolved_entities", {})), {}, []
    for mention in state.get("entity_mentions", []):
        if mention in resolved:
            continue
        rows = service.search(mention, context=state.get("question", ""))
        candidates[mention] = rows
        selected_id = service.auto_resolve(rows)
        if selected_id:
            resolved[mention] = selected_id
        elif rows:
            ambiguous[mention] = rows
        else:
            not_found.append(mention)

    if not_found:
        logger.info("Entity Resolution: ENTITY_NOT_FOUND %s", not_found)
        emit_event("ENTITY_NOT_FOUND", thread_id=state.get("thread_id"), mentions=not_found)
        interrupt({"status": "ENTITY_NOT_FOUND", "mentions": not_found, "candidates": candidates,
                   "instruction": "未找到实体，请检查名称或补充机构、职称、研究方向后重新提问"})

    if ambiguous:
        logger.info("Entity Resolution: NEED_USER_SELECTION %s", list(ambiguous))
        emit_event("ENTITY_RESOLUTION_INTERRUPTED", thread_id=state.get("thread_id"), mentions=list(ambiguous))
        selections = interrupt({"status": "NEED_USER_SELECTION", "candidates": ambiguous,
                                "instruction": "请为每个姓名选择一个 entity_id"})
        if not isinstance(selections, dict):
            raise ValueError("恢复执行时必须传入 {姓名: entity_id}")
        missing_selections = set(ambiguous) - set(selections)
        if missing_selections:
            raise ValueError(f"缺少实体选择: {', '.join(sorted(missing_selections))}")
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
            "entity_candidates": candidates, "awaiting_user_selection": False,
            "entity_resolution_status": "RESOLVED", "unresolved_mentions": []}
