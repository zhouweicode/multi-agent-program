"""结构化事件日志，便于按 thread/node/agent/tool 检索执行轨迹。"""
import json
import logging
import time
from functools import wraps
from datetime import datetime, timezone
from threading import Lock
from collections import defaultdict, deque
from contextlib import contextmanager
from typing import Any, Iterator
from langgraph.errors import GraphInterrupt

logger = logging.getLogger("graphrag.events")
_event_lock = Lock()
_events: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=500))


def serializable_snapshot(value: Any) -> Any:
    """生成不会被后续 State 更新影响、且可由事件 API 返回的完整快照。"""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def emit_event(event: str, **fields: Any) -> None:
    payload = {"event": event, "timestamp": datetime.now(timezone.utc).isoformat(), **fields}
    thread_id = fields.get("thread_id")
    if thread_id:
        with _event_lock:
            bucket = _events[str(thread_id)]
            payload["sequence"] = bucket[-1]["sequence"] + 1 if bucket else 1
            bucket.append(payload.copy())
    logger.info(json.dumps(payload, ensure_ascii=False, default=str))


def clear_events(thread_id: str) -> None:
    """开始同 ID 的新查询前清空旧轨迹。"""
    with _event_lock:
        _events.pop(thread_id, None)


def get_events(thread_id: str, after: int = 0) -> list[dict[str, Any]]:
    """返回 sequence 大于 after 的事件快照。"""
    with _event_lock:
        return [item.copy() for item in _events.get(thread_id, ()) if item["sequence"] > after]


def traced_node(node_name: str, node):
    """为每个 LangGraph Node 统一记录完整输入 State 与输出 State Update。"""
    @wraps(node)
    def wrapped(state):
        node_input = serializable_snapshot(state)
        thread_id = state.get("thread_id")
        try:
            output = node(state)
        except GraphInterrupt:
            emit_event("NODE_INTERRUPTED", thread_id=thread_id, node_name=node_name,
                       node_input=node_input, node_output={"status": "INTERRUPTED"})
            raise
        except Exception as exc:
            emit_event("NODE_FAILED", thread_id=thread_id, node_name=node_name,
                       node_input=node_input,
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
