"""Vendor-neutral trace context, durable span recording and model usage accounting."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Iterator
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler

from models.settings import Settings
from repositories.observability_repository import SQLiteObservabilityRepository


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identifier() -> str:
    return uuid4().hex


@dataclass(frozen=True)
class TraceContext:
    run_id: str
    attempt_id: int
    trace_id: str
    root_span_id: str
    started_at: str
    started_perf: float
    parent_trace_id: str | None = None


_trace_context: ContextVar[TraceContext | None] = ContextVar("graphrag_trace_context", default=None)
_current_span_id: ContextVar[str | None] = ContextVar("graphrag_current_span_id", default=None)
_repository: SQLiteObservabilityRepository | None = None
_repository_lock = Lock()


def repository() -> SQLiteObservabilityRepository:
    global _repository
    with _repository_lock:
        if _repository is None:
            path = os.getenv("OBSERVABILITY_DB_PATH", ".runtime/observability.sqlite")
            _repository = SQLiteObservabilityRepository(path)
        return _repository


def reset_repository_for_tests(path: str | None = None) -> None:
    global _repository
    with _repository_lock:
        if _repository is not None:
            _repository.close()
        if path:
            os.environ["OBSERVABILITY_DB_PATH"] = path
        _repository = None


def run_metadata(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or Settings.from_env()
    return {
        "git_sha": os.getenv("APP_GIT_SHA", "local"),
        "workflow_version": settings.workflow_version,
        "prompt_version": settings.prompt_version,
        "model_provider": settings.model_provider,
        "model_name": settings.model_name,
        "tool_transport": settings.tool_transport,
        "entity_backend": settings.entity_backend,
        "graph_backend": settings.graph_backend,
    }


def prepare_run_trace(run_id: str, metadata: dict[str, Any] | None = None) -> TraceContext:
    attempt_id, parent_trace_id = repository().next_attempt(run_id)
    context = TraceContext(run_id=run_id, attempt_id=attempt_id, trace_id=_identifier(),
                           root_span_id=_identifier(), started_at=_utc_now(), started_perf=time.perf_counter(),
                           parent_trace_id=parent_trace_id)
    settings = Settings.from_env()
    repository().start_trace({
        "run_id": run_id,
        "attempt_id": attempt_id,
        "trace_id": context.trace_id,
        "root_span_id": context.root_span_id,
        "parent_trace_id": parent_trace_id,
        "started_at": context.started_at,
        "cost_currency": settings.model_cost_currency,
        "metadata": metadata or run_metadata(settings),
    })
    return context


@contextmanager
def activate_trace(context: TraceContext, parent_span_id: str | None = None) -> Iterator[TraceContext]:
    trace_token = _trace_context.set(context)
    span_token = _current_span_id.set(parent_span_id or context.root_span_id)
    try:
        yield context
    finally:
        _current_span_id.reset(span_token)
        _trace_context.reset(trace_token)


@contextmanager
def activate_remote_trace(carrier: dict[str, Any] | None) -> Iterator[TraceContext | None]:
    if not carrier or not carrier.get("trace_id"):
        yield None
        return
    context = TraceContext(
        run_id=str(carrier["run_id"]), attempt_id=int(carrier["attempt_id"]),
        trace_id=str(carrier["trace_id"]), root_span_id=str(carrier.get("root_span_id") or _identifier()),
        started_at=str(carrier.get("started_at") or _utc_now()), started_perf=time.perf_counter(),
        parent_trace_id=carrier.get("parent_trace_id"),
    )
    with activate_trace(context, str(carrier.get("parent_span_id") or context.root_span_id)):
        yield context


def trace_carrier() -> dict[str, Any] | None:
    context = _trace_context.get()
    if context is None:
        return None
    return {
        "run_id": context.run_id,
        "attempt_id": context.attempt_id,
        "trace_id": context.trace_id,
        "root_span_id": context.root_span_id,
        "parent_trace_id": context.parent_trace_id,
        "parent_span_id": _current_span_id.get() or context.root_span_id,
        "started_at": context.started_at,
    }


@dataclass
class SpanHandle:
    name: str
    kind: str
    attributes: dict[str, Any] = field(default_factory=dict)
    context: TraceContext | None = None
    span_id: str = field(default_factory=_identifier)
    parent_span_id: str | None = None
    started_at: str = field(default_factory=_utc_now)
    started_perf: float = field(default_factory=time.perf_counter)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    status: str = "OK"
    error_type: str | None = None
    _token: Any = None
    _finished: bool = False

    def __enter__(self) -> "SpanHandle":
        self.context = self.context or _trace_context.get()
        self.parent_span_id = self.parent_span_id or _current_span_id.get()
        if self.context is not None:
            self._token = _current_span_id.set(self.span_id)
        return self

    def set_attribute(self, name: str, value: Any) -> None:
        self.attributes[name] = value

    def set_usage(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.input_tokens = max(0, int(input_tokens or 0))
        self.output_tokens = max(0, int(output_tokens or 0))
        self.total_tokens = self.input_tokens + self.output_tokens
        settings = Settings.from_env()
        self.cost = round(
            (self.input_tokens * settings.model_input_cost_per_million
             + self.output_tokens * settings.model_output_cost_per_million) / 1_000_000,
            8,
        )

    def record_error(self, error: BaseException) -> None:
        self.status = "ERROR"
        self.error_type = type(error).__name__
        self.attributes.setdefault("error.message", str(error)[:1000])

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        if self._token is not None:
            _current_span_id.reset(self._token)
            self._token = None
        if self.context is None:
            return
        repository().add_span({
            "span_id": self.span_id,
            "trace_id": self.context.trace_id,
            "run_id": self.context.run_id,
            "attempt_id": self.context.attempt_id,
            "parent_span_id": self.parent_span_id or self.context.root_span_id,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": _utc_now(),
            "duration_ms": round((time.perf_counter() - self.started_perf) * 1000, 3),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost": self.cost,
            "error_type": self.error_type,
            "attributes": self.attributes,
        })

    def __exit__(self, exc_type, exc, _tb) -> bool:
        if exc is not None:
            self.record_error(exc)
        self.finish()
        return False


def traced_span(name: str, kind: str, attributes: dict[str, Any] | None = None) -> SpanHandle:
    return SpanHandle(name=name, kind=kind, attributes=dict(attributes or {}))


def finish_run_trace(context: TraceContext, status: str, replan_count: int = 0) -> None:
    repository().finish_trace(
        context.trace_id,
        status=status,
        ended_at=_utc_now(),
        duration_ms=round((time.perf_counter() - context.started_perf) * 1000, 3),
        replan_count=replan_count,
    )


def trace_fields() -> dict[str, Any]:
    context = _trace_context.get()
    if context is None:
        return {}
    return {"trace_id": context.trace_id, "span_id": _current_span_id.get(),
            "attempt_id": context.attempt_id}


def _usage_from_result(result: Any) -> tuple[int, int]:
    usage = getattr(result, "llm_output", None) or {}
    token_usage = usage.get("token_usage") or usage.get("usage") or {}
    input_tokens = token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0
    output_tokens = token_usage.get("completion_tokens") or token_usage.get("output_tokens") or 0
    if input_tokens or output_tokens:
        return int(input_tokens), int(output_tokens)
    for generation_list in getattr(result, "generations", []) or []:
        for generation in generation_list:
            message_usage = getattr(getattr(generation, "message", None), "usage_metadata", None) or {}
            input_tokens += int(message_usage.get("input_tokens", 0) or 0)
            output_tokens += int(message_usage.get("output_tokens", 0) or 0)
    return int(input_tokens), int(output_tokens)


class TelemetryCallbackHandler(BaseCallbackHandler):
    """Captures ChatModel latency and usage without coupling business code to a provider SDK."""
    def __init__(self):
        self._spans: dict[str, SpanHandle] = {}
        self._lock = Lock()

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:
        span = traced_span("gen_ai.chat", "model", {
            "gen_ai.operation.name": "chat",
            "gen_ai.model": (kwargs.get("invocation_params") or {}).get("model_name"),
        })
        # LangChain 的 start/end callback 可能由不同异步 Context 执行。这里创建 detached span，
        # 显式捕获父上下文，避免在 end callback 中 reset 另一个 Context 的 token。
        span.context = _trace_context.get()
        span.parent_span_id = _current_span_id.get()
        with self._lock:
            self._spans[str(run_id)] = span

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        with self._lock:
            span = self._spans.pop(str(run_id), None)
        if span:
            span.set_usage(*_usage_from_result(response))
            span.finish()

    def on_llm_error(self, error, *, run_id, **kwargs) -> None:
        with self._lock:
            span = self._spans.pop(str(run_id), None)
        if span:
            span.record_error(error)
            span.finish()


telemetry_callback = TelemetryCallbackHandler()
