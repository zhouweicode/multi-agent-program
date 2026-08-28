"""Authenticated long-term memory administration without exposing storage details."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from services.long_term_memory import sanitize_memory_text
from services.memory_manager import RepositoryMemoryManager, memory_manager
from services.observability import emit_event

MEMORY_CATEGORIES = (
    "preference", "focus", "correction", "constraint", "output_format", "context",
)
_CATEGORY_ORDER = {category: index for index, category in enumerate((
    "correction", "constraint", "output_format", "preference", "focus", "context",
))}


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _storage_time(value: datetime | None,
                  manager: RepositoryMemoryManager) -> str | datetime | None:
    if value is None:
        return None
    normalized = value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC)
    if normalized <= datetime.now(UTC):
        raise ValueError("有效期必须晚于当前时间")
    if manager.backend == "mysql":
        return normalized.replace(tzinfo=None)
    return normalized.isoformat()


def validate_manual_content(content: str) -> str:
    content = " ".join(str(content or "").split()).strip()
    if not content:
        raise ValueError("记忆内容不能为空")
    if len(content) > 500:
        raise ValueError("记忆内容不能超过 500 个字符")
    sanitized, sensitive = sanitize_memory_text(content, 500)
    if sensitive or sanitized != content:
        raise ValueError("记忆内容不能包含密码、Token、邮箱、手机号或身份证号")
    return content


def list_user_facts(user_id: str, query: str = "", category: str | None = None,
                    limit: int = 100,
                    include_archived: bool = False,
                    manager: RepositoryMemoryManager | None = None) -> list[dict[str, Any]]:
    manager = manager or memory_manager()
    rows = manager.list_facts(
        user_id, min(max(limit, 1), 500), include_archived=include_archived
    )
    needle = query.strip().casefold()
    if needle:
        rows = [row for row in rows if needle in str(row.get("content") or "").casefold()]
    if category:
        rows = [row for row in rows if row.get("category") == category]
    now = datetime.now(UTC)
    public = []
    for row in rows:
        item = dict(row)
        expiry = _parse_time(item.get("expected_valid_until"))
        item["expired"] = bool(expiry and expiry <= now)
        item["review_status"] = (
            "archived" if item.get("status") == "archived"
            else "due" if item["expired"] else "current"
        )
        public.append(item)
    public.sort(key=lambda row: (
        bool(row["expired"]), _CATEGORY_ORDER.get(str(row.get("category")), 99),
        str(row.get("updated_at") or ""), str(row.get("fact_id") or ""),
    ))
    return public[:min(max(limit, 1), 500)]


def memory_summary(user_id: str,
                   manager: RepositoryMemoryManager | None = None) -> dict[str, Any]:
    manager = manager or memory_manager()
    facts = list_user_facts(user_id, limit=500, manager=manager)
    categories = Counter(str(fact.get("category") or "context") for fact in facts)
    active = [fact for fact in facts if not fact["expired"]]
    sections = {
        category: [fact["content"] for fact in active if fact.get("category") == category][:3]
        for category in MEMORY_CATEGORIES
    }
    return {
        "user_id": user_id,
        "fact_count": len(facts),
        "active_fact_count": len(active),
        "expired_fact_count": len(facts) - len(active),
        "review_due_count": sum(fact["review_status"] == "due" for fact in facts),
        "total_recall_count": sum(int(fact.get("recall_count") or 0) for fact in facts),
        "total_application_count": sum(
            int(fact.get("application_count") or 0) for fact in facts
        ),
        "category_counts": dict(categories),
        "summary": sections,
        "last_updated_at": max(
            (str(fact.get("updated_at") or "") for fact in facts), default=None
        ),
        "backend": manager.backend,
    }


def create_manual_fact(user_id: str, content: str, category: str,
                       confidence: float = 1.0,
                       expected_valid_until: datetime | None = None,
                       manager: RepositoryMemoryManager | None = None) -> dict[str, Any]:
    manager = manager or memory_manager()
    if category not in MEMORY_CATEGORIES:
        raise ValueError("不支持的记忆分类")
    fact = manager.create_fact(
        user_id, validate_manual_content(content), category=category,
        confidence=confidence, source_run_id="manual",
        expected_valid_until=_storage_time(expected_valid_until, manager),
    )
    emit_event("LONG_TERM_MEMORY_MANUALLY_CREATED", user_id=user_id,
               fact_id=fact["fact_id"], category=category)
    return fact


def update_manual_fact(user_id: str, fact_id: str, changes: dict[str, Any],
                       manager: RepositoryMemoryManager | None = None,
                       expected_revision: int | None = None) -> dict[str, Any]:
    manager = manager or memory_manager()
    normalized = dict(changes)
    if "content" in normalized:
        normalized["content"] = validate_manual_content(normalized["content"])
    if "category" in normalized and normalized["category"] not in MEMORY_CATEGORIES:
        raise ValueError("不支持的记忆分类")
    if "expected_valid_until" in normalized:
        normalized["expected_valid_until"] = _storage_time(
            normalized["expected_valid_until"], manager
        )
    fact = manager.update_fact(
        user_id, fact_id, normalized, expected_revision=expected_revision
    )
    emit_event("LONG_TERM_MEMORY_MANUALLY_UPDATED", user_id=user_id,
               fact_id=fact_id, changed_fields=sorted(normalized))
    return fact


def delete_manual_fact(user_id: str, fact_id: str,
                       expected_revision: int | None = None,
                       manager: RepositoryMemoryManager | None = None) -> bool:
    manager = manager or memory_manager()
    deleted = manager.delete_fact(
        user_id, fact_id, expected_revision=expected_revision
    )
    if deleted:
        emit_event("LONG_TERM_MEMORY_MANUALLY_DELETED", user_id=user_id,
                   fact_id=fact_id)
    return deleted


def export_user_memory(user_id: str,
                       manager: RepositoryMemoryManager | None = None) -> dict[str, Any]:
    manager = manager or memory_manager()
    return {
        "schema_version": 2,
        "exported_at": datetime.now(UTC).isoformat(),
        "user_id": user_id,
        "summary": memory_summary(user_id, manager),
        "facts": list_user_facts(user_id, limit=500, manager=manager),
        "audit_logs": manager.list_audit_logs(user_id, limit=500),
    }
