"""结构化事件日志，便于按 thread/node/agent/tool 检索执行轨迹。"""
import json
import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger("graphrag.events")


def emit_event(event: str, **fields: Any) -> None:
    logger.info(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))


@contextmanager
def timed_event(event: str, **fields: Any) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
        emit_event(event, status="completed", duration_ms=round((time.perf_counter() - started) * 1000, 2), **fields)
    except Exception:
        emit_event(event, status="failed", duration_ms=round((time.perf_counter() - started) * 1000, 2), **fields)
        raise
