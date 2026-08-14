"""SQLite Checkpointer 生命周期管理；API 重启后可按 thread_id 恢复。"""
import sqlite3
from pathlib import Path
from langgraph.checkpoint.sqlite import SqliteSaver
from models.settings import Settings

_connection: sqlite3.Connection | None = None


def build_sqlite_checkpointer(path: str | None = None) -> SqliteSaver:
    global _connection
    db_path = Path(path or Settings.from_env().checkpoint_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _connection = sqlite3.connect(db_path, check_same_thread=False)
    return SqliteSaver(_connection)
