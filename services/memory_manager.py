"""Backend-neutral memory contract and runtime manager factory."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

from models.settings import Settings
from repositories.conversation_memory_repository import (
    SQLiteConversationMemoryRepository,
)
from repositories.long_term_memory_repository import SQLiteLongTermMemoryRepository
from repositories.query_experience_repository import SQLiteQueryExperienceRepository
from services.memory_lifecycle import (
    choose_eviction_candidate,
    decide_lifecycle_action,
)

logger = logging.getLogger(__name__)


class MemoryManager(ABC):
    """Unified boundary for conversation, long-term and experience memory."""

    backend: str

    @abstractmethod
    def recall_context(self, user_id: str, conversation_id: str | None,
                       query: str = "", top_k: int = 0,
                       agent_name: str | None = None) -> dict[str, Any]: ...

    @abstractmethod
    def record_turn(self, **turn: Any) -> dict[str, Any]: ...

    @abstractmethod
    def enqueue_update(self, user_id: str, run_id: str, payload: dict[str, Any],
                       conversation_id: str | None = None,
                       agent_name: str | None = None) -> bool: ...

    @abstractmethod
    def search_facts(self, user_id: str, query: str, top_k: int = 5,
                     agent_name: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def create_fact(self, user_id: str, content: str, **metadata: Any) -> dict[str, Any]: ...

    @abstractmethod
    def update_fact(self, user_id: str, fact_id: str, changes: dict[str, Any],
                    agent_name: str | None = None,
                    expected_revision: int | None = None) -> dict[str, Any]: ...

    @abstractmethod
    def delete_fact(self, user_id: str, fact_id: str,
                    agent_name: str | None = None,
                    expected_revision: int | None = None) -> bool: ...

    @abstractmethod
    def clear_memory(self, user_id: str, conversation_id: str | None = None,
                     agent_name: str | None = None) -> dict[str, Any]: ...

    @abstractmethod
    def flush(self) -> bool: ...

    @abstractmethod
    def close(self) -> None: ...


class RepositoryMemoryManager(MemoryManager):
    """Compose repository adapters behind one stable application contract."""

    def __init__(self, conversation: Any, experience: Any, long_term: Any,
                 backend: str, fact_index: Any | None = None,
                 settings: Settings | None = None):
        self.conversation = conversation
        self.experience = experience
        self.long_term = long_term
        self.backend = backend
        self.fact_index = fact_index
        self.settings = settings or Settings.from_env()

    def recall_context(self, user_id: str, conversation_id: str | None,
                       query: str = "", top_k: int = 0,
                       agent_name: str | None = None) -> dict[str, Any]:
        conversation = None
        if conversation_id:
            conversation = self.conversation.get(user_id, conversation_id)
        facts = self.search_facts(user_id, query, top_k, agent_name) if top_k > 0 else []
        return {"conversation": conversation, "facts": facts, "backend": self.backend}

    def ensure_conversation(self, user_id: str, conversation_id: str) -> None:
        self.conversation.ensure_conversation(user_id, conversation_id)

    def conversation_exists(self, user_id: str, conversation_id: str) -> bool:
        return self.conversation.exists(user_id, conversation_id)

    def get_conversation(self, user_id: str, conversation_id: str,
                         turn_limit: int = 10) -> dict[str, Any]:
        return self.conversation.get(user_id, conversation_id, turn_limit)

    def record_turn(self, **turn: Any) -> dict[str, Any]:
        return self.conversation.record_turn(**turn)

    def enqueue_update(self, user_id: str, run_id: str, payload: dict[str, Any],
                       conversation_id: str | None = None,
                       agent_name: str | None = None) -> bool:
        return self.long_term.enqueue(
            user_id, run_id, payload, conversation_id, agent_name
        )

    def claim_update_jobs(self, limit: int = 10,
                          lease_seconds: int = 60) -> list[dict[str, Any]]:
        return self.long_term.claim_jobs(limit, lease_seconds)

    def complete_update_job(self, job_id: str) -> bool:
        return self.long_term.complete_job(job_id)

    def fail_update_job(self, job_id: str, error: str,
                        retry_after_seconds: int,
                        terminal: bool = False) -> bool:
        return self.long_term.fail_job(
            job_id, error, retry_after_seconds, terminal
        )

    def update_job_stats(self) -> dict[str, int]:
        return self.long_term.job_stats()

    def search_facts(self, user_id: str, query: str, top_k: int = 5,
                     agent_name: str | None = None) -> list[dict[str, Any]]:
        authoritative = self.long_term.search(user_id, query, top_k, agent_name)
        if not self.fact_index or not query.strip():
            return authoritative
        try:
            hits = self.fact_index.search(user_id, query, top_k, agent_name)
            rows: list[dict[str, Any]] = []
            seen: set[str] = set()
            getter = getattr(self.long_term, "get_fact", None) or self.long_term.get
            for hit in hits:
                try:
                    fact = getter(user_id, hit["fact_id"], agent_name)
                except KeyError:
                    continue
                rows.append(fact | {
                    "vector_score": hit.get("vector_score"),
                    "retrieval_method": hit.get("retrieval_method"),
                })
                seen.add(str(fact["fact_id"]))
            rows.extend(fact for fact in authoritative
                        if str(fact["fact_id"]) not in seen)
            return rows[:max(1, min(top_k, 100))]
        except Exception:  # vector index is rebuildable and optional
            logger.exception("长期记忆 Milvus 检索失败，回退权威存储")
            return authoritative

    def list_facts(self, user_id: str, limit: int = 100,
                   agent_name: str | None = None,
                   include_archived: bool = False) -> list[dict[str, Any]]:
        list_facts = getattr(self.long_term, "list_facts", None)
        if list_facts:
            return list_facts(user_id, limit, agent_name, include_archived)
        return self.long_term.search(user_id, "", limit, agent_name)

    def create_fact(self, user_id: str, content: str, **metadata: Any) -> dict[str, Any]:
        agent_name = metadata.get("agent_name")
        if not metadata.get("expected_valid_until"):
            review_at = datetime.now(UTC) + timedelta(
                days=self.settings.memory_fact_review_days
            )
            metadata["expected_valid_until"] = (
                review_at.replace(tzinfo=None)
                if self.backend == "mysql" else review_at.isoformat()
            )
        existing = self.list_facts(
            user_id, self.settings.memory_fact_max_per_scope + 1, agent_name
        )
        category = str(metadata.get("category") or "context")
        decision = decide_lifecycle_action(
            existing, content, category,
            self.settings.memory_fact_similarity_threshold,
        )
        if decision.target:
            target = decision.target
            changes = {
                "content": content,
                "category": category,
                "confidence": max(
                    float(target.get("confidence") or 0),
                    float(metadata.get("confidence") or 0),
                ),
                "expected_valid_until": metadata["expected_valid_until"],
                "status": "active",
                "source_run_id": metadata.get("source_run_id"),
                "source_conversation_id": metadata.get("source_conversation_id"),
            }
            fact = self.long_term.update(
                user_id, str(target["fact_id"]), changes, agent_name,
                expected_revision=int(target.get("revision") or 1),
            )
            self._index_facts([fact])
            self._audit(user_id, f"fact_{decision.action}", "memory_fact",
                        str(fact["fact_id"]), {
                            "reason": decision.reason,
                            "similarity": decision.similarity,
                            "previous_revision": target.get("revision"),
                            "revision": fact.get("revision"),
                        }, agent_name)
            return fact | {"lifecycle_action": decision.action}

        if len(existing) >= self.settings.memory_fact_max_per_scope:
            evicted = choose_eviction_candidate(existing)
            if evicted:
                self.delete_fact(
                    user_id, str(evicted["fact_id"]), agent_name,
                    audit_operation="fact_capacity_evicted",
                )
        fact = self.long_term.create(user_id, content, **metadata)
        self._index_facts([fact])
        self._audit(user_id, "fact_created", "memory_fact", str(fact["fact_id"]), {
            "category": fact.get("category"), "revision": fact.get("revision"),
        }, agent_name)
        return fact | {"lifecycle_action": "create"}

    def update_fact(self, user_id: str, fact_id: str, changes: dict[str, Any],
                    agent_name: str | None = None,
                    expected_revision: int | None = None) -> dict[str, Any]:
        fact = self.long_term.update(
            user_id, fact_id, changes, agent_name,
            expected_revision=expected_revision,
        )
        self._index_facts([fact])
        self._audit(user_id, "fact_updated", "memory_fact", fact_id, {
            "changed_fields": sorted(changes), "revision": fact.get("revision"),
            "expected_revision": expected_revision,
        }, agent_name)
        return fact

    def delete_fact(self, user_id: str, fact_id: str,
                    agent_name: str | None = None,
                    audit_operation: str = "fact_deleted",
                    expected_revision: int | None = None) -> bool:
        deleted = self.long_term.delete(
            user_id, fact_id, agent_name, expected_revision=expected_revision
        )
        if deleted and self.fact_index:
            try:
                self.fact_index.delete_facts([fact_id])
            except Exception:  # index is rebuildable
                logger.exception("长期记忆 Milvus 删除同步失败")
        if deleted:
            self._audit(user_id, audit_operation, "memory_fact", fact_id, {
                "expected_revision": expected_revision,
            }, agent_name)
        return deleted

    def mark_facts_recalled(self, user_id: str, fact_ids: list[str],
                            agent_name: str | None = None) -> int:
        count = self.long_term.mark_recalled(user_id, fact_ids, agent_name)
        if count:
            self._audit(user_id, "facts_recalled", "memory_fact_batch", None,
                        {"fact_ids": fact_ids, "count": count}, agent_name)
        return count

    def mark_facts_applied(self, user_id: str, fact_ids: list[str],
                           agent_name: str | None = None) -> int:
        count = self.long_term.mark_applied(user_id, fact_ids, agent_name)
        if count:
            self._audit(user_id, "facts_applied", "memory_fact_batch", None,
                        {"fact_ids": fact_ids, "count": count}, agent_name)
        return count

    def review_fact(self, user_id: str, fact_id: str, action: str,
                    expected_revision: int, review_days: int | None = None,
                    agent_name: str | None = None) -> dict[str, Any]:
        if action not in {"renew", "archive"}:
            raise ValueError("复核操作必须是 renew 或 archive")
        if action == "renew":
            days = max(1, min(review_days or self.settings.memory_fact_review_days, 3650))
            expiry = datetime.now(UTC) + timedelta(days=days)
            changes = {
                "status": "active",
                "expected_valid_until": (
                    expiry.replace(tzinfo=None) if self.backend == "mysql"
                    else expiry.isoformat()
                ),
            }
        else:
            changes = {"status": "archived"}
        fact = self.update_fact(
            user_id, fact_id, changes, agent_name, expected_revision
        )
        self._audit(user_id, f"fact_review_{action}", "memory_fact", fact_id, {
            "revision": fact.get("revision"), "review_days": review_days,
        }, agent_name)
        return fact

    def list_audit_logs(self, user_id: str, limit: int = 100,
                        agent_name: str | None = None) -> list[dict[str, Any]]:
        return self.long_term.list_audit_logs(user_id, limit, agent_name)

    def _audit(self, user_id: str, operation: str, target_type: str,
               target_id: str | None, metadata: dict[str, Any],
               agent_name: str | None = None) -> None:
        try:
            self.long_term.audit(
                user_id, operation, target_type, target_id, metadata, agent_name
            )
        except Exception:  # audit failure must not corrupt an already committed fact
            logger.exception("记忆审计写入失败 operation=%s", operation)

    def clear_memory(self, user_id: str, conversation_id: str | None = None,
                     agent_name: str | None = None) -> dict[str, Any]:
        if conversation_id:
            return self.conversation.clear(user_id, conversation_id)
        fact_ids = [str(fact["fact_id"]) for fact in
                    self.long_term.search(user_id, "", 500, agent_name)]
        deleted = self.long_term.clear_facts(user_id, agent_name)
        if self.fact_index:
            try:
                delete_user_facts = getattr(self.fact_index, "delete_user_facts", None)
                if delete_user_facts:
                    delete_user_facts(user_id, agent_name)
                elif fact_ids:
                    self.fact_index.delete_facts(fact_ids)
            except Exception:  # index is rebuildable
                logger.exception("长期记忆 Milvus 批量删除同步失败")
        self._audit(user_id, "facts_cleared", "memory_fact_scope", None,
                    {"deleted_facts": deleted}, agent_name)
        return {"user_id": user_id, "cleared": True, "deleted_facts": deleted}

    def clear_all_personal_memory(self, user_id: str) -> dict[str, Any]:
        result = self.clear_memory(user_id)
        result.update(self.conversation.clear_user_conversations(user_id))
        result.update(self.experience.clear_experience_scope("user", user_id))
        result["deleted_update_jobs"] = self.long_term.clear_update_jobs(user_id)
        clear_profile = getattr(self.long_term, "clear_profile", None)
        result["deleted_profiles"] = clear_profile(user_id) if clear_profile else 0
        return result

    def _index_facts(self, facts: list[dict[str, Any]]) -> None:
        if not self.fact_index:
            return
        try:
            self.fact_index.upsert_facts(facts)
        except Exception:  # MySQL stays authoritative
            logger.exception("长期记忆 Milvus 索引同步失败，权威事实已保留")

    def record_experience(self, event: dict[str, Any]) -> bool:
        return self.experience.record(event)

    def finalize_experience_metrics(self, run_id: str, metrics: dict[str, Any]) -> bool:
        return self.experience.finalize_metrics(run_id, metrics)

    def list_experience_patterns(self, scope_type: str, scope_id: str,
                                 limit: int = 100,
                                 positive_only: bool = False) -> list[dict[str, Any]]:
        return self.experience.list_patterns(scope_type, scope_id, limit, positive_only)

    def get_experience_pattern(self, pattern_id: str) -> dict[str, Any] | None:
        return self.experience.get_pattern(pattern_id)

    def experience_stats(self, scope_type: str, scope_id: str) -> dict[str, Any]:
        return self.experience.stats(scope_type, scope_id)

    def flush(self) -> bool:
        return True

    def health(self) -> dict[str, Any]:
        health = getattr(self.conversation, "health", None)
        if callable(health):
            result = health()
        else:
            result = {"backend": self.backend, "ready": True}
        if self.fact_index:
            result["memory_fact_index"] = self.fact_index.health()
        return result

    def close(self) -> None:
        closed: set[int] = set()
        for repository in (self.conversation, self.experience, self.long_term):
            if id(repository) in closed:
                continue
            repository.close()
            closed.add(id(repository))
        if self.fact_index and id(self.fact_index) not in closed:
            self.fact_index.close()


_manager: RepositoryMemoryManager | None = None
_manager_signature: tuple[Any, ...] | None = None
_manager_lock = Lock()


def _signature(settings: Settings) -> tuple[Any, ...]:
    if settings.memory_backend == "mysql":
        return ("mysql", settings.mysql_host, settings.mysql_port,
                settings.memory_mysql_database, settings.mysql_user,
                settings.memory_retrieval_backend, settings.memory_milvus_uri,
                settings.memory_milvus_collection)
    return ("sqlite", settings.conversation_memory_db_path,
            settings.query_experience_db_path, settings.long_term_memory_db_path,
            settings.memory_retrieval_backend, settings.memory_milvus_uri,
            settings.memory_milvus_collection)


def _build_manager(settings: Settings) -> RepositoryMemoryManager:
    fact_index = None
    if settings.memory_retrieval_backend in {"hybrid", "milvus"}:
        try:
            from repositories.milvus_memory_repository import (
                MilvusMemoryFactRepository,
            )
            fact_index = MilvusMemoryFactRepository(settings)
        except Exception:
            if settings.memory_retrieval_backend == "milvus":
                raise
            logger.exception("长期记忆 Milvus 初始化失败，使用 MySQL/SQLite 回退")
    if settings.memory_backend == "mysql":
        from repositories.mysql_memory_repository import MySQLMemoryRepository

        repository = MySQLMemoryRepository(settings)
        return RepositoryMemoryManager(
            repository, repository, repository, "mysql", fact_index, settings
        )
    if settings.memory_backend != "sqlite":
        raise ValueError(f"不支持的 MEMORY_BACKEND: {settings.memory_backend}")
    return RepositoryMemoryManager(
        SQLiteConversationMemoryRepository(settings.conversation_memory_db_path),
        SQLiteQueryExperienceRepository(settings.query_experience_db_path),
        SQLiteLongTermMemoryRepository(settings.long_term_memory_db_path),
        "sqlite",
        fact_index,
        settings,
    )


def memory_manager() -> RepositoryMemoryManager:
    global _manager, _manager_signature
    settings = Settings.from_env()
    signature = _signature(settings)
    with _manager_lock:
        if _manager is not None and _manager_signature == signature:
            return _manager
        if _manager is not None:
            _manager.close()
        _manager = _build_manager(settings)
        _manager_signature = signature
        return _manager


def close_memory_manager() -> None:
    global _manager, _manager_signature
    with _manager_lock:
        manager = _manager
        _manager = None
        _manager_signature = None
    if manager is not None:
        manager.flush()
        manager.close()
