"""Password hashing, seeded users and persistent browser sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from models.settings import Settings
from repositories.user_repository import SQLiteUserRepository

PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 600_000
SESSION_COOKIE_NAME = "graphrag_session"


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if not password:
        raise ValueError("密码不能为空")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return "$".join(
        (
            PASSWORD_ALGORITHM,
            str(PASSWORD_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, expected_raw = encoded.split("$", 3)
        if algorithm != PASSWORD_ALGORITHM:
            return False
        iterations = int(iterations_raw)
        salt = base64.urlsafe_b64decode(salt_raw.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_raw.encode("ascii"))
    except (TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(actual, expected)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.repository = SQLiteUserRepository(settings.auth_db_path)
        self._seed_users()

    def _seed_users(self) -> None:
        seeds = (
            (
                "user-admin",
                "admin",
                "系统管理员",
                self.settings.auth_admin_password,
            ),
            (
                "user-researcher",
                "researcher",
                "科研用户",
                self.settings.auth_researcher_password,
            ),
            (
                "user-analyst",
                "analyst",
                "分析用户",
                self.settings.auth_analyst_password,
            ),
        )
        for user_id, username, display_name, password in seeds:
            # Seed only once. Environment changes never silently reset an
            # existing account's persisted password hash.
            if self.repository.get_user(user_id) is None:
                self.repository.create_user(
                    user_id=user_id,
                    username=username,
                    display_name=display_name,
                    password_hash=hash_password(password),
                )

    def list_users(self) -> list[dict[str, Any]]:
        return self.repository.list_active_users()

    def login(self, user_id: str, password: str) -> tuple[str, dict[str, Any]] | None:
        user = self.repository.get_user(user_id)
        if not user or not user.get("active"):
            # Run a dummy hash to reduce observable differences between an
            # unknown account and a bad password.
            hash_password(password or "invalid")
            return None
        if not verify_password(password, str(user["password_hash"])):
            return None
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=self.settings.auth_session_ttl_seconds
        )
        self.repository.create_session(
            token_hash=_token_hash(token),
            user_id=user_id,
            expires_at=expires_at.isoformat(),
        )
        public_user = {
            key: user[key]
            for key in ("user_id", "username", "display_name")
        }
        return token, public_user | {"active": True}

    def authenticate(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        return self.repository.get_session_user(_token_hash(token))

    def logout(self, token: str | None) -> None:
        if token:
            self.repository.delete_session(_token_hash(token))

    def close(self) -> None:
        self.repository.close()


_services: dict[str, AuthService] = {}
_service_lock = Lock()


def auth_service() -> AuthService:
    settings = Settings.from_env()
    with _service_lock:
        service = _services.get(settings.auth_db_path)
        if service is None:
            service = AuthService(settings)
            _services[settings.auth_db_path] = service
        return service


def close_auth_services() -> None:
    with _service_lock:
        services = list(_services.values())
        _services.clear()
    for service in services:
        service.close()
