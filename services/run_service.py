"""后台 Graph Run 管理器：隔离 run_id、保存状态并驱动异步执行。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable
from collections import OrderedDict
from services.observability import emit_event


class RunManager:
    TERMINAL = {"COMPLETED", "FAILED", "NEED_USER_SELECTION"}

    def __init__(self, max_workers: int = 4, max_runs: int = 100):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="graphrag-run")
        self._lock = Lock()
        self._runs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max_runs = max_runs

    def exists(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._runs

    def create(self, run_id: str) -> None:
        with self._lock:
            if run_id in self._runs:
                raise ValueError("run_id 已存在")
            if len(self._runs) >= self._max_runs:
                removable = next((key for key, value in self._runs.items() if value["status"] in self.TERMINAL), None)
                if removable is None:
                    raise RuntimeError("后台运行队列已满")
                self._runs.pop(removable)
            self._runs[run_id] = {"run_id": run_id, "status": "RUNNING", "result": None,
                                  "error": None, "updated_at": datetime.now(timezone.utc).isoformat()}

    def mark_running(self, run_id: str) -> None:
        self._update(run_id, status="RUNNING", result=None, interrupt=None, error=None)

    def submit(self, run_id: str, operation: Callable[[], dict]) -> None:
        self._executor.submit(self._execute, run_id, operation)

    def _execute(self, run_id: str, operation: Callable[[], dict]) -> None:
        try:
            result = operation()
            interrupts = result.get("__interrupt__", ())
            status = "NEED_USER_SELECTION" if interrupts else "COMPLETED"
            interrupt = interrupts[0].value if interrupts else None
            self._update(run_id, status=status, result=result, interrupt=interrupt, error=None)
            emit_event("RUN_STATUS_CHANGED", thread_id=run_id, status=status)
        except Exception as exc:
            self._update(run_id, status="FAILED", error={"type": type(exc).__name__, "message": str(exc)})
            emit_event("QUERY_FAILED", thread_id=run_id, error_type=type(exc).__name__)

    def _update(self, run_id: str, **values: Any) -> None:
        with self._lock:
            self._runs[run_id].update(values)
            self._runs[run_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._runs.move_to_end(run_id)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._runs.get(run_id)
            return deepcopy(record) if record else None

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
