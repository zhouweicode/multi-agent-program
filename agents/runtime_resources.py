"""Process-wide bounded executors, bulkheads and Tool circuit state."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


class RuntimeCapacityError(RuntimeError):
    """A bounded executor or provider bulkhead has no capacity."""


class CircuitOpenError(RuntimeError):
    """A Tool is blocked by its shared circuit breaker."""


class BoundedExecutor:
    """Thread pool with a hard upper bound on running plus queued work."""

    def __init__(self, max_workers: int, max_queue: int, thread_name_prefix: str):
        workers = max(1, max_workers)
        queued = max(0, max_queue)
        self.max_workers = workers
        self.max_queue = queued
        self._slots = threading.BoundedSemaphore(workers + queued)
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix=thread_name_prefix
        )

    def submit(
        self,
        function: Callable[..., Any],
        *args: Any,
        acquire_timeout: float = 0,
        **kwargs: Any,
    ) -> Future:
        acquired = self._slots.acquire(
            timeout=max(0.0, acquire_timeout)
        )
        if not acquired:
            raise RuntimeCapacityError(
                f"调用线程池已饱和: workers={self.max_workers}, queue={self.max_queue}"
            )
        try:
            future = self._executor.submit(function, *args, **kwargs)
        except BaseException:
            self._slots.release()
            raise
        future.add_done_callback(lambda _future: self._slots.release())
        return future

    def invoke(
        self,
        function: Callable[..., Any],
        *args: Any,
        timeout: float = 0,
        acquire_timeout: float = 0,
        **kwargs: Any,
    ) -> Any:
        future = self.submit(
            function, *args, acquire_timeout=acquire_timeout, **kwargs
        )
        try:
            return future.result(timeout=timeout if timeout > 0 else None)
        except FutureTimeout as exc:
            future.cancel()
            raise TimeoutError(f"调用超过 {timeout:g}s") from exc

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


class AsyncLoopRunner:
    """One shared event loop for cancellable native async model and Tool calls."""

    def __init__(self, max_inflight: int, thread_name: str = "agent-async"):
        self.max_inflight = max(1, max_inflight)
        self._slots = threading.BoundedSemaphore(self.max_inflight)
        self._loop = asyncio.new_event_loop()
        self._started = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop, name=thread_name, daemon=True
        )
        self._thread.start()
        self._started.wait()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._started.set()
        self._loop.run_forever()

    def invoke(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        timeout: float,
        acquire_timeout: float,
    ) -> Any:
        if not self._slots.acquire(timeout=max(0.0, acquire_timeout)):
            raise RuntimeCapacityError(
                f"异步调用容量已饱和: max_inflight={self.max_inflight}"
            )
        try:
            awaitable = operation()
            future = asyncio.run_coroutine_threadsafe(awaitable, self._loop)
        except BaseException:
            self._slots.release()
            raise
        future.add_done_callback(lambda _future: self._slots.release())
        try:
            return future.result(timeout=timeout if timeout > 0 else None)
        except FutureTimeout as exc:
            future.cancel()
            raise TimeoutError(f"异步调用超过 {timeout:g}s") from exc

    def shutdown(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=0.2)


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float = 0.0
    half_open_probe: bool = False


class ToolHealthRegistry:
    """Shared circuit state and per-provider in-flight call counters."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._circuits: dict[str, _CircuitState] = {}
        self._provider_inflight: dict[str, int] = defaultdict(int)

    def enter_circuit(
        self, key: str, *, threshold: int, reset_seconds: float
    ) -> None:
        limit = max(1, threshold)
        with self._condition:
            state = self._circuits.setdefault(key, _CircuitState())
            if state.failures < limit:
                return
            elapsed = time.monotonic() - state.opened_at
            if elapsed < max(0.0, reset_seconds) or state.half_open_probe:
                raise CircuitOpenError(f"CIRCUIT_OPEN:{key}")
            state.half_open_probe = True

    def record_outcome(
        self, key: str, *, success: bool, transient_failure: bool = False
    ) -> None:
        with self._condition:
            state = self._circuits.setdefault(key, _CircuitState())
            if success:
                state.failures = 0
                state.opened_at = 0.0
                state.half_open_probe = False
                return
            if transient_failure:
                state.failures += 1
                state.opened_at = time.monotonic()
            state.half_open_probe = False

    def abandon_probe(self, key: str) -> None:
        with self._condition:
            state = self._circuits.get(key)
            if state:
                state.half_open_probe = False

    @contextmanager
    def provider_slot(
        self, provider: str, *, limit: int, acquire_timeout: float
    ) -> Iterator[None]:
        capacity = max(1, limit)
        deadline = time.monotonic() + max(0.0, acquire_timeout)
        with self._condition:
            while self._provider_inflight[provider] >= capacity:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeCapacityError(
                        f"Provider 并发已达上限: {provider} ({capacity})"
                    )
                self._condition.wait(remaining)
            self._provider_inflight[provider] += 1
        try:
            yield
        finally:
            with self._condition:
                self._provider_inflight[provider] -= 1
                self._condition.notify_all()

    def reset(self) -> None:
        with self._condition:
            self._circuits.clear()
            self._provider_inflight.clear()
            self._condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                "circuits": {
                    key: {
                        "failures": state.failures,
                        "opened_at": state.opened_at,
                        "half_open_probe": state.half_open_probe,
                    }
                    for key, state in self._circuits.items()
                },
                "provider_inflight": dict(self._provider_inflight),
            }


_lock = threading.Lock()
_health_registry: ToolHealthRegistry | None = None
_invocation_executor: BoundedExecutor | None = None
_orchestration_executor: BoundedExecutor | None = None
_async_runner: AsyncLoopRunner | None = None


def shared_tool_health_registry() -> ToolHealthRegistry:
    global _health_registry
    with _lock:
        if _health_registry is None:
            _health_registry = ToolHealthRegistry()
        return _health_registry


def shared_invocation_executor() -> BoundedExecutor:
    global _invocation_executor
    with _lock:
        if _invocation_executor is None:
            _invocation_executor = BoundedExecutor(
                int(os.getenv("AGENT_INVOCATION_MAX_WORKERS", "32")),
                int(os.getenv("AGENT_INVOCATION_MAX_QUEUE", "64")),
                "agent-invoke",
            )
        return _invocation_executor


def shared_orchestration_executor() -> BoundedExecutor:
    global _orchestration_executor
    with _lock:
        if _orchestration_executor is None:
            _orchestration_executor = BoundedExecutor(
                int(os.getenv("AGENT_ORCHESTRATION_MAX_WORKERS", "16")),
                int(os.getenv("AGENT_ORCHESTRATION_MAX_QUEUE", "32")),
                "agent-orchestrate",
            )
        return _orchestration_executor


def shared_async_runner() -> AsyncLoopRunner:
    global _async_runner
    with _lock:
        if _async_runner is None:
            _async_runner = AsyncLoopRunner(
                int(os.getenv("AGENT_ASYNC_MAX_INFLIGHT", "64"))
            )
        return _async_runner


def close_shared_runtime_resources() -> None:
    """Release process-wide pools after callers have stopped accepting work."""
    global _health_registry, _invocation_executor, _orchestration_executor, _async_runner
    with _lock:
        if _invocation_executor:
            _invocation_executor.shutdown()
        if _orchestration_executor:
            _orchestration_executor.shutdown()
        if _async_runner:
            _async_runner.shutdown()
        _health_registry = None
        _invocation_executor = None
        _orchestration_executor = None
        _async_runner = None


def reset_shared_runtime_resources() -> None:
    """Test lifecycle alias; existing Harness instances keep old handles."""
    close_shared_runtime_resources()
