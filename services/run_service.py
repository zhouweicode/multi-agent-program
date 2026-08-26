"""后台 Graph Run 管理器：隔离 run_id、保存状态并驱动异步执行。"""
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock, Timer
from time import monotonic
from typing import Any, Callable
from collections import OrderedDict
from repositories.run_repository import SQLiteRunRepository
from services.run_control import RunCancelledError, clear_run, raise_if_stopped, register_run, request_stop
from services.observability import emit_event
from services.telemetry import TraceContext, activate_trace, finish_run_trace, traced_span


class RunManager:
    TERMINAL = {"COMPLETED", "FAILED", "NEED_USER_SELECTION", "ENTITY_NOT_FOUND", "CANCELLED", "TIMED_OUT"}

    def __init__(self, max_workers: int = 4, max_runs: int = 100, timeout_seconds: float = 120,
                 repository: SQLiteRunRepository | None = None):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="graphrag-run")
        self._lock = Lock()
        self._runs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._futures: dict[str, Future] = {}
        self._timers: dict[str, Timer] = {}
        self._trace_contexts: dict[str, TraceContext] = {}
        self._max_runs = max_runs
        self._timeout_seconds = timeout_seconds
        self._repository = repository
        if self._repository:
            self._repository.recover_incomplete()

    def exists(self, run_id: str) -> bool:
        with self._lock:
            in_memory = run_id in self._runs
        return in_memory or bool(self._repository and self._repository.exists(run_id))

    def create(self, run_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            if run_id in self._runs:
                raise ValueError("run_id 已存在")
            if len(self._runs) >= self._max_runs:
                removable = next((key for key, value in self._runs.items() if value["status"] in self.TERMINAL), None)
                if removable is None:
                    raise RuntimeError("后台运行队列已满")
                self._runs.pop(removable)
            self._runs[run_id] = {"run_id": run_id, "status": "RUNNING", "result": None,
                                  "error": None, "metrics": {}, "created_at": now, "updated_at": now}
            record = deepcopy(self._runs[run_id])
        self._persist(record)

    def mark_running(self, run_id: str) -> None:
        with self._lock:
            if run_id not in self._runs:
                persisted = self._repository.get(run_id) if self._repository else None
                if not persisted:
                    raise KeyError(run_id)
                self._runs[run_id] = persisted | {"result": None, "interrupt": None}
        self._update(run_id, status="RUNNING", result=None, interrupt=None, error=None)

    def submit(self, run_id: str, operation: Callable[[], dict], trace_context: TraceContext | None = None) -> None:
        register_run(run_id)
        timer = Timer(self._timeout_seconds, self._request_timeout, args=(run_id,))
        timer.daemon = True
        with self._lock:
            # 持锁提交，避免极短任务在线程完成清理后，主线程才把 Future/Trace 写回字典。
            if trace_context is not None:
                self._trace_contexts[run_id] = trace_context
            future = self._executor.submit(self._execute, run_id, operation, trace_context)
            self._futures[run_id] = future
            self._timers[run_id] = timer
        timer.start()

    def fail(self, run_id: str, error: BaseException) -> None:
        """记录后台任务提交前的失败，避免 Run 永久停留在 RUNNING。"""
        if self.get(run_id):
            self._update(run_id, status="FAILED",
                         error={"type": type(error).__name__, "message": str(error)})

    def _request_timeout(self, run_id: str) -> None:
        record = self.get(run_id)
        if record and record["status"] not in self.TERMINAL:
            request_stop(run_id, "TIMED_OUT")
            self._update(run_id, status="CANCELLING")
            emit_event("RUN_TIMEOUT_REQUESTED", thread_id=run_id, timeout_seconds=self._timeout_seconds)

    def cancel(self, run_id: str) -> bool:
        record = self.get(run_id)
        if not record or record["status"] in self.TERMINAL:
            return False
        request_stop(run_id, "CANCELLED")
        with self._lock:
            future = self._futures.get(run_id)
        if future and future.cancel():
            self._update(run_id, status="CANCELLED")
            with self._lock:
                trace_context = self._trace_contexts.pop(run_id, None)
                timer = self._timers.pop(run_id, None)
                self._futures.pop(run_id, None)
            if timer:
                timer.cancel()
            if trace_context:
                finish_run_trace(trace_context, "CANCELLED")
            clear_run(run_id)
            emit_event("RUN_STATUS_CHANGED", thread_id=run_id, status="CANCELLED")
            return True
        self._update(run_id, status="CANCELLING")
        emit_event("RUN_CANCEL_REQUESTED", thread_id=run_id)
        return True

    def _execute(self, run_id: str, operation: Callable[[], dict], trace_context: TraceContext | None = None) -> None:
        if trace_context is None:
            self._execute_impl(run_id, operation)
            return
        try:
            with activate_trace(trace_context):
                with traced_span("graphrag.workflow.execute", "workflow", {"run.id": run_id}) as span:
                    self._execute_impl(run_id, operation)
                    record = self.get(run_id) or {}
                    span.set_attribute("run.status", record.get("status"))
                    if record.get("status") == "FAILED":
                        span.status = "ERROR"
                        span.error_type = (record.get("error") or {}).get("type")
        finally:
            record = self.get(run_id) or {}
            result = record.get("result") or {}
            finish_run_trace(trace_context, record.get("status", "FAILED"), int(result.get("replan_count", 0) or 0))
            with self._lock:
                self._trace_contexts.pop(run_id, None)

    def _execute_impl(self, run_id: str, operation: Callable[[], dict]) -> None:
        started = monotonic()
        try:
            result = operation()
            raise_if_stopped(run_id)
            interrupts = result.get("__interrupt__", ())
            interrupt = interrupts[0].value if interrupts else None
            status = interrupt.get("status", "NEED_USER_SELECTION") if isinstance(interrupt, dict) else (
                "NEED_USER_SELECTION" if interrupts else "COMPLETED")
            self._update(run_id, status=status, result=result, interrupt=interrupt, error=None,
                         metrics={"duration_ms": round((monotonic() - started) * 1000, 2)})
            emit_event("RUN_STATUS_CHANGED", thread_id=run_id, status=status)
        except RunCancelledError as exc:
            self._update(run_id, status=exc.reason, error=None,
                         metrics={"duration_ms": round((monotonic() - started) * 1000, 2)})
            emit_event("RUN_STATUS_CHANGED", thread_id=run_id, status=exc.reason)
        except Exception as exc:
            self._update(run_id, status="FAILED", error={"type": type(exc).__name__, "message": str(exc)},
                         metrics={"duration_ms": round((monotonic() - started) * 1000, 2)})
            emit_event("QUERY_FAILED", thread_id=run_id, error_type=type(exc).__name__)
        finally:
            with self._lock:
                timer = self._timers.pop(run_id, None)
                self._futures.pop(run_id, None)
            if timer:
                timer.cancel()
            clear_run(run_id)

    def _update(self, run_id: str, **values: Any) -> None:
        with self._lock:
            self._runs[run_id].update(values)
            self._runs[run_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._runs.move_to_end(run_id)
            record = deepcopy(self._runs[run_id])
        self._persist(record)

    def _persist(self, record: dict[str, Any]) -> None:
        if self._repository:
            self._repository.upsert(record)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._runs.get(run_id)
            if record:
                return deepcopy(record)
        return self._repository.get(run_id) if self._repository else None

    def close(self) -> None:
        with self._lock:
            active = [run_id for run_id, future in self._futures.items() if not future.done()]
            timers = list(self._timers.values())
        for timer in timers:
            timer.cancel()
        for run_id in active:
            self.cancel(run_id)
        self._executor.shutdown(wait=True, cancel_futures=True)
        if self._repository:
            self._repository.close()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            records = list(self._runs.values())
        counts: dict[str, int] = {}
        for record in records:
            counts[record["status"]] = counts.get(record["status"], 0) + 1
        durations = [record.get("metrics", {}).get("duration_ms") for record in records
                     if record.get("metrics", {}).get("duration_ms") is not None]
        return {"in_memory_runs": len(records), "status_counts": counts,
                "average_duration_ms": round(sum(durations) / len(durations), 2) if durations else None,
                "max_workers": self._executor._max_workers, "timeout_seconds": self._timeout_seconds}
