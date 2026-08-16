"""结构化事件与受控节点快照：脱敏、限长、按线程淘汰。"""
import json
import logging
import os
import time
from collections import OrderedDict, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from threading import Lock
from typing import Any, Iterator

from langgraph.errors import GraphInterrupt
from services.run_control import raise_if_stopped

logger = logging.getLogger("graphrag.events")
_event_lock = Lock()
_max_threads = int(os.getenv("TRACE_MAX_THREADS", "100"))
_max_events = int(os.getenv("TRACE_MAX_EVENTS_PER_THREAD", "200"))
_max_snapshot_chars = int(os.getenv("TRACE_MAX_SNAPSHOT_CHARS", "200000"))
_log_payloads = os.getenv("TRACE_LOG_PAYLOADS", "false").lower() == "true"
_events: OrderedDict[str, deque[dict[str, Any]]] = OrderedDict()
_sensitive_fragments = ("password", "passwd", "secret", "api_key", "apikey", "token", "authorization")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "***REDACTED***" if any(fragment in key.lower() for fragment in _sensitive_fragments) else _redact(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


def serializable_snapshot(value: Any) -> Any:
    """生成稳定、脱敏、大小受控的事件快照。"""
    normalized = json.loads(json.dumps(_redact(value), ensure_ascii=False, default=str))
    encoded = json.dumps(normalized, ensure_ascii=False)
    if len(encoded) <= _max_snapshot_chars:
        return normalized
    return {"_truncated": True, "original_chars": len(encoded),
            "preview": encoded[:_max_snapshot_chars]}


def emit_event(event: str, **fields: Any) -> None:
    payload = {"event": event, "timestamp": datetime.now(timezone.utc).isoformat(), **serializable_snapshot(fields)}
    thread_id = fields.get("thread_id")
    if thread_id:
        key = str(thread_id)
        with _event_lock:
            bucket = _events.setdefault(key, deque(maxlen=_max_events))
            payload["sequence"] = bucket[-1]["sequence"] + 1 if bucket else 1
            bucket.append(payload.copy())
            _events.move_to_end(key)
            while len(_events) > _max_threads:
                _events.popitem(last=False)
    if _log_payloads:
        logger.info(json.dumps(payload, ensure_ascii=False, default=str))
    else:
        logger.info("event=%s thread_id=%s sequence=%s", event, thread_id, payload.get("sequence"))


def clear_events(thread_id: str) -> None:
    with _event_lock:
        _events.pop(thread_id, None)


def get_events(thread_id: str, after: int = 0) -> list[dict[str, Any]]:
    with _event_lock:
        bucket = _events.get(thread_id, ())
        return [item.copy() for item in bucket if item["sequence"] > after]


def traced_node(node_name: str, node):
    """为每个 LangGraph Node 记录输入 State 和输出 State Update。"""
    @wraps(node)
    def wrapped(state):
        node_input = serializable_snapshot(state)
        thread_id = state.get("thread_id")
        try:
            raise_if_stopped(thread_id)
            output = node(state)
            raise_if_stopped(thread_id)
        except GraphInterrupt:
            emit_event("NODE_INTERRUPTED", thread_id=thread_id, node_name=node_name,
                       node_input=node_input, node_output={"status": "INTERRUPTED"})
            raise
        except Exception as exc:
            emit_event("NODE_FAILED", thread_id=thread_id, node_name=node_name, node_input=node_input,
                       node_output={"status": "FAILED", "error_type": type(exc).__name__, "error": str(exc)})
            raise
        emit_event("NODE_EXECUTED", thread_id=thread_id, node_name=node_name,
                   node_input=node_input, node_output=serializable_snapshot(output))
        return output
    return wrapped


@contextmanager
def timed_event(event: str, **fields: Any) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
        emit_event(event, status="completed", duration_ms=round((time.perf_counter() - started) * 1000, 2), **fields)
    except Exception:
        emit_event(event, status="failed", duration_ms=round((time.perf_counter() - started) * 1000, 2), **fields)
        raise
