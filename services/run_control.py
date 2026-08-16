"""后台 Run 的协作式取消/超时信号；Node 边界会主动检查。"""
from threading import Event, Lock


class RunCancelledError(RuntimeError):
    def __init__(self, reason: str = "CANCELLED"):
        self.reason = reason
        super().__init__("查询已取消" if reason == "CANCELLED" else "查询执行超时")


_lock = Lock()
_signals: dict[str, tuple[Event, str]] = {}


def register_run(run_id: str) -> None:
    with _lock:
        _signals[run_id] = (Event(), "CANCELLED")


def request_stop(run_id: str, reason: str) -> None:
    with _lock:
        event, _ = _signals.setdefault(run_id, (Event(), reason))
        _signals[run_id] = (event, reason)
        event.set()


def raise_if_stopped(run_id: str | None) -> None:
    if not run_id:
        return
    with _lock:
        signal = _signals.get(run_id)
    if signal and signal[0].is_set():
        raise RunCancelledError(signal[1])


def clear_run(run_id: str) -> None:
    with _lock:
        _signals.pop(run_id, None)
