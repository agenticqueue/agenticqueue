from __future__ import annotations

import re
import time
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.config import (
    get_psycopg_connect_args,
    get_sqlalchemy_sync_database_url,
)
from agenticqueue_api.local_auth import hash_password
from agenticqueue_api.migrations import apply_database_migrations

AUTH_TRUNCATE_TABLES = [
    "auth_audit_log",
    "auth_sessions",
    "auth_rate_limit",
    "project_members",
    "users",
    "api_token",
    "actor",
]
NON_ADMIN_ACTOR_SQL = """
INSERT INTO agenticqueue.actor
    (handle, actor_type, display_name, auth_subject, is_active)
VALUES
    ('operator', 'human', 'Operator', 'local:operator@localhost', true)
RETURNING id
"""
TOKEN_HASH_ROWS_SQL = """
SELECT token_hash, name
FROM agenticqueue.api_token
"""


@pytest.fixture(scope="session")
def engine() -> Engine:
    engine = sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        future=True,
        connect_args=get_psycopg_connect_args(),
    )
    apply_database_migrations()
    return engine


@pytest.fixture(autouse=True)
def clean_auth_tables(engine: Engine) -> Iterator[None]:
    with engine.begin() as connection:
        qualified_tables = ", ".join(
            f"agenticqueue.{table_name}" for table_name in AUTH_TRUNCATE_TABLES
        )
        connection.execute(
            sa.text(f"TRUNCATE TABLE {qualified_tables} RESTART IDENTITY CASCADE")
        )
    yield


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app(session_factory=session_factory)
    with TestClient(app) as test_client:
        yield test_client


def _bootstrap(client: TestClient) -> str:
    response = client.post(
        "/api/auth/bootstrap_admin",
        json={
            "email": "admin@localhost",
            "password": "CorrectHorse12!",
        },
    )
    assert response.status_code == 200
    session_cookie = response.cookies.get("aq_session")
    assert session_cookie is not None
    client.cookies.set("aq_session", session_cookie)
    return response.json()["first_token"]


def _create_token(client: TestClient, name: str = "codex") -> dict[str, str]:
    response = client.post("/api/auth/tokens", json={"name": name})
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == name
    assert re.fullmatch(r"aq_live_[A-Za-z0-9]{32,}", payload["token"])
    return payload


def _seed_non_admin(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        actor_id = session.scalar(sa.text(NON_ADMIN_ACTOR_SQL))
        session.execute(
            sa.text("""
                INSERT INTO agenticqueue.users
                    (email, passcode_hash, actor_id, is_admin, is_active)
                VALUES
                    (:email, :passcode_hash, :actor_id, false, true)
                """),
            {
                "email": "operator@localhost",
                "passcode_hash": hash_password("CorrectHorse12!"),
                "actor_id": actor_id,
            },
        )


def _sign_in_non_admin(client: TestClient) -> None:
    response = client.post(
        "/api/session",
        json={"email": "operator@localhost", "password": "CorrectHorse12!"},
    )
    assert response.status_code == 200
    session_cookie = response.cookies.get("aq_session")
    assert session_cookie is not None
    client.cookies.set("aq_session", session_cookie)


def test_create_token(client: TestClient) -> None:
    _bootstrap(client)

    payload = _create_token(client, name="codex")

    assert payload["token"].startswith("aq_live_")
    assert payload["id"]
    assert payload["created_at"]
    assert payload["token_preview"] == f"{payload['token'][:8]}..."
    assert "token_hash" not in payload


def test_list_tokens(client: TestClient) -> None:
    _bootstrap(client)
    created = _create_token(client, name="codex")

    response = client.get("/api/auth/tokens")

    assert response.status_code == 200
    payload = response.json()
    tokens = payload["tokens"]
    assert [token["name"] for token in tokens] == ["bootstrap", "codex"]
    codex = next(token for token in tokens if token["name"] == "codex")
    assert codex["id"] == created["id"]
    assert codex["token_preview"] == f"{created['token'][:8]}..."
    assert codex["last_used_at"] is None
    assert "token" not in codex
    assert created["token"] not in response.text


def test_list_tokens_non_admin_403(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _seed_non_admin(session_factory)
    _sign_in_non_admin(client)

    response = client.get("/api/auth/tokens")

    assert response.status_code == 403


def test_revoke_token(client: TestClient) -> None:
    _bootstrap(client)
    created = _create_token(client, name="codex")

    before_revoke = client.get(
        "/v1/auth/tokens",
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    assert before_revoke.status_code == 200

    response = client.delete(f"/api/auth/tokens/{created['id']}")

    assert response.status_code == 204
    after_revoke = client.get(
        "/v1/auth/tokens",
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    assert after_revoke.status_code == 401


def test_last_used_updates(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _bootstrap(client)
    created = _create_token(client, name="codex")
    with session_factory() as session:
        before = session.scalar(
            sa.text("""
                SELECT last_used_at
                FROM agenticqueue.api_token
                WHERE id = :id
                """),
            {"id": created["id"]},
        )
    assert before is None

    time.sleep(1.1)
    response = client.get(
        "/v1/auth/tokens",
        headers={"Authorization": f"Bearer {created['token']}"},
    )

    assert response.status_code == 200
    with session_factory() as session:
        after = session.scalar(
            sa.text("""
                SELECT last_used_at
                FROM agenticqueue.api_token
                WHERE id = :id
                """),
            {"id": created["id"]},
        )
    assert after is not None


def test_bootstrap_token_in_list(client: TestClient) -> None:
    _bootstrap(client)

    response = client.get("/api/auth/tokens")

    assert response.status_code == 200
    tokens = response.json()["tokens"]
    assert len(tokens) == 1
    assert tokens[0]["name"] == "bootstrap"
    assert tokens[0]["token_preview"].startswith("aq_live_")


def test_tokens_stored_hashed(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _bootstrap(client)
    created = _create_token(client, name="codex")

    with session_factory() as session:
        rows = session.execute(sa.text(TOKEN_HASH_ROWS_SQL)).all()

    serialized_rows = repr(rows)
    assert created["token"] not in serialized_rows
    assert all(created["token"] not in row.token_hash for row in rows)
    assert all(row.token_hash != created["token"] for row in rows)
