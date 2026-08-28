"""Durable background consumer for passive long-term memory extraction."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any

from models.settings import Settings
from services.long_term_memory import ExtractionResult, extract_long_term_facts
from services.memory_manager import RepositoryMemoryManager, memory_manager
from services.observability import emit_event


class MemoryUpdateWorker:
    def __init__(
        self,
        manager_factory: Callable[[], RepositoryMemoryManager] = memory_manager,
        extractor: Callable[[dict[str, Any]], ExtractionResult] = extract_long_term_facts,
        settings: Settings | None = None,
    ):
        self.manager_factory = manager_factory
        self.extractor = extractor
        self.settings = settings or Settings.from_env()
        self._stop = Event()
        self._thread: Thread | None = None

    def process_once(self) -> dict[str, int]:
        result = {"claimed": 0, "completed": 0, "retried": 0,
                  "failed": 0, "facts_written": 0}
        manager = self.manager_factory()
        jobs = manager.claim_update_jobs(
            self.settings.memory_worker_batch_size,
            self.settings.memory_worker_lease_seconds,
        )
        result["claimed"] = len(jobs)
        for job in jobs:
            try:
                extraction = self.extractor(job["payload"])
                for fact in extraction.facts:
                    if fact.confidence < self.settings.memory_fact_confidence_threshold:
                        continue
                    manager.create_fact(
                        job["user_id"],
                        fact.content,
                        category=fact.category,
                        confidence=fact.confidence,
                        agent_name=job.get("agent_name") or None,
                        source_run_id=job["run_id"],
                        source_conversation_id=job.get("conversation_id"),
                    )
                    result["facts_written"] += 1
                manager.complete_update_job(job["job_id"])
                result["completed"] += 1
                emit_event(
                    "LONG_TERM_MEMORY_EXTRACTED",
                    thread_id=job["run_id"],
                    job_id=job["job_id"],
                    fact_count=len(extraction.facts),
                    rejected_count=extraction.rejected_count,
                    sensitive=extraction.sensitive,
                )
            except Exception as exc:  # noqa: BLE001 - durable queue owns retries
                attempts = int(job.get("attempt_count") or 1)
                terminal = attempts >= self.settings.memory_worker_max_attempts
                manager.fail_update_job(
                    job["job_id"],
                    f"{type(exc).__name__}: {exc}",
                    retry_after_seconds=min(2 ** attempts, 60),
                    terminal=terminal,
                )
                result["failed" if terminal else "retried"] += 1
                emit_event(
                    "LONG_TERM_MEMORY_EXTRACTION_FAILED",
                    thread_id=job["run_id"],
                    job_id=job["job_id"],
                    attempt_count=attempts,
                    terminal=terminal,
                    error_type=type(exc).__name__,
                )
        return result

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                processed = self.process_once()["claimed"]
            except Exception as exc:  # noqa: BLE001 - worker must never crash app
                processed = 0
                emit_event("LONG_TERM_MEMORY_WORKER_ERROR", error_type=type(exc).__name__)
            if processed == 0:
                self._stop.wait(self.settings.memory_worker_poll_seconds)

    def start(self) -> bool:
        if not self.settings.memory_extraction_enabled:
            return False
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = Thread(target=self._run, name="memory-update-worker", daemon=True)
        self._thread.start()
        return True

    def drain(self, timeout: float = 10) -> dict[str, int]:
        """Synchronously flush currently available jobs during graceful shutdown."""
        aggregate = {"claimed": 0, "completed": 0, "retried": 0,
                     "failed": 0, "facts_written": 0}
        deadline = monotonic() + max(0, timeout)
        while monotonic() < deadline:
            result = self.process_once()
            for key, value in result.items():
                aggregate[key] += value
            if result["claimed"] == 0:
                break
        return aggregate

    def stop(self, timeout: float = 10, flush: bool = True) -> dict[str, int]:
        self._stop.set()
        thread = self._thread
        if thread:
            thread.join(timeout=max(0, timeout))
        self._thread = None
        if thread and thread.is_alive():
            return {"claimed": 0, "completed": 0, "retried": 0,
                    "failed": 0, "facts_written": 0}
        return self.drain(timeout) if flush else {
            "claimed": 0, "completed": 0, "retried": 0,
            "failed": 0, "facts_written": 0,
        }


_worker: MemoryUpdateWorker | None = None
_worker_lock = Lock()


def start_memory_update_worker() -> MemoryUpdateWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = MemoryUpdateWorker()
        _worker.start()
        return _worker


def stop_memory_update_worker() -> dict[str, int]:
    global _worker
    with _worker_lock:
        worker = _worker
        _worker = None
    if worker is not None:
        return worker.stop()
    return {"claimed": 0, "completed": 0, "retried": 0,
            "failed": 0, "facts_written": 0}
