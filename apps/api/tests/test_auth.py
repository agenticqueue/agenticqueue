from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.config import get_sqlalchemy_sync_database_url

AUTH_TRUNCATE_TABLES = [
    "auth_audit_log",
    "auth_sessions",
    "auth_rate_limit",
    "project_members",
    "users",
    "actor",
]
TEST_SALT_HEX = "0f" * 16


def _password_hash(password: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(TEST_SALT_HEX),
        200_000,
    )
    return f"pbkdf2_sha256$200000${TEST_SALT_HEX}${digest.hex()}"


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


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


def seed_admin(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        actor_insert = sa.text("""
                INSERT INTO agenticqueue.actor
                    (handle, actor_type, display_name, auth_subject, is_active)
                VALUES
                    ('admin', 'admin', 'Admin', 'local:admin', true)
                RETURNING id
                """)
        actor_id = session.scalar(actor_insert)
        session.execute(
            sa.text("""
                INSERT INTO agenticqueue.users
                    (email, passcode_hash, actor_id, is_admin, is_active)
                VALUES
                    (:email, :passcode_hash, :actor_id, true, true)
                """),
            {
                "email": "admin@localhost",
                "passcode_hash": _password_hash("letmein-dev-passcode"),
                "actor_id": actor_id,
            },
        )
        session.commit()


def test_session_accepts_email_password_and_sets_cookie(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    seed_admin(session_factory)

    response = client.post(
        "/api/session",
        json={"email": "ADMIN@LOCALHOST", "password": "letmein-dev-passcode"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {"email": "admin@localhost", "is_admin": True}
    set_cookie = response.headers["set-cookie"]
    assert "aq_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    assert "Max-Age=604800" in set_cookie

    with session_factory() as session:
        session_query = sa.text("""
                SELECT expires_at, revoked_at
                FROM agenticqueue.auth_sessions
                """)
        session_row = session.execute(session_query).one()

    assert session_row.revoked_at is None
    assert session_row.expires_at > dt.datetime.now(dt.UTC)


def test_session_rejects_username_field(client: TestClient) -> None:
    response = client.post(
        "/api/session",
        json={"username": "admin", "password": "letmein-dev-passcode"},
    )

    assert response.status_code == 422
