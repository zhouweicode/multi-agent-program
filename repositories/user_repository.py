"""SQLite-backed users and login sessions."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteUserRepository:
    """Persist demo accounts and opaque browser sessions.

    Password hashing belongs to the authentication service. This repository
    only stores the resulting hash and never receives a plaintext password.
    """

    def __init__(self, path: str):
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = Lock()
        with self._connection:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
                    ON auth_sessions(user_id, expires_at);
                """
            )

    @staticmethod
    def _public_user(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "user_id": row["user_id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "active": bool(row["active"]),
        }

    def create_user(
        self,
        *,
        user_id: str,
        username: str,
        display_name: str,
        password_hash: str,
    ) -> bool:
        now = _now()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO users(
                    user_id, username, display_name, password_hash,
                    active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (user_id, username, display_name, password_hash, now, now),
            )
        return bool(cursor.rowcount)

    def list_active_users(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT user_id, username, display_name, active
                FROM users WHERE active=1 ORDER BY created_at, username
                """
            ).fetchall()
        return [self._public_user(row) for row in rows]

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
        return dict(row) if row else None

    def create_session(
        self, *, token_hash: str, user_id: str, expires_at: str
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO auth_sessions(token_hash, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (token_hash, user_id, _now(), expires_at),
            )

    def get_session_user(self, token_hash: str) -> dict[str, Any] | None:
        now = _now()
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM auth_sessions WHERE expires_at <= ?", (now,)
            )
            row = self._connection.execute(
                """
                SELECT u.user_id, u.username, u.display_name, u.active,
                       s.expires_at
                FROM auth_sessions s
                JOIN users u ON u.user_id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>? AND u.active=1
                """,
                (token_hash, now),
            ).fetchone()
        if not row:
            return None
        return self._public_user(row) | {"expires_at": row["expires_at"]}

    def delete_session(self, token_hash: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,)
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()
