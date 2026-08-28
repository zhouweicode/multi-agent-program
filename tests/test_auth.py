"""Multi-user authentication and user identity propagation tests."""

import sqlite3

from fastapi.testclient import TestClient

from app.main import app
from models.settings import Settings
from services.auth import SESSION_COOKIE_NAME
from tests.helpers import wait_for_run


def _login(client: TestClient, user_id: str, password: str):
    return client.post(
        "/auth/login",
        json={"user_id": user_id, "password": password},
    )


def test_login_user_choices_do_not_expose_credentials():
    client = TestClient(app)
    response = client.get("/auth/users")
    assert response.status_code == 200
    users = response.json()["users"]
    assert [(user["user_id"], user["username"]) for user in users] == [
        ("user-admin", "admin"),
        ("user-researcher", "researcher"),
        ("user-analyst", "analyst"),
    ]
    assert all("password" not in key for user in users for key in user)


def test_login_session_survives_a_new_browser_client(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    client = TestClient(app)
    assert _login(client, "user-researcher", "wrong-password").status_code == 401

    logged_in = _login(client, "user-researcher", "Research@123")
    assert logged_in.status_code == 200
    assert logged_in.json()["user"]["display_name"] == "科研用户"
    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token

    restored = TestClient(app)
    restored.cookies.set(SESSION_COOKIE_NAME, token)
    current = restored.get("/auth/me")
    assert current.status_code == 200
    assert current.json()["user"]["user_id"] == "user-researcher"


def test_protected_query_injects_authenticated_user_id(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    client = TestClient(app)
    assert client.get("/metrics").status_code == 401
    assert client.post(
        "/queries",
        json={"question": "查询人工智能产业链TOP事件。", "thread_id": "auth-query-denied"},
    ).status_code == 401

    assert _login(client, "user-analyst", "Analyst@123").status_code == 200
    run_id = "auth-query-user-id"
    created = client.post(
        "/queries",
        json={"question": "查询人工智能产业链TOP事件。", "thread_id": run_id},
    )
    assert created.status_code == 202
    assert created.json()["user_id"] == "user-analyst"
    completed = wait_for_run(client, run_id, {"COMPLETED"})
    assert completed["state"]["user_id"] == "user-analyst"

    assert client.post("/auth/logout").status_code == 200
    assert client.get("/metrics").status_code == 401


def test_persisted_users_store_hashes_not_plaintext_passwords():
    client = TestClient(app)
    client.get("/auth/users")
    connection = sqlite3.connect(Settings.from_env().auth_db_path)
    try:
        rows = connection.execute(
            "SELECT username, password_hash FROM users ORDER BY username"
        ).fetchall()
    finally:
        connection.close()
    assert len(rows) == 3
    assert all(password_hash.startswith("pbkdf2_sha256$") for _, password_hash in rows)
    plaintext = {"Admin@123", "Research@123", "Analyst@123"}
    assert all(password_hash not in plaintext for _, password_hash in rows)
