from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import authenticate_api_token
from agenticqueue_api.config import get_sqlalchemy_sync_database_url

TRUNCATE_TABLES = [
    "auth_audit_log",
    "auth_sessions",
    "auth_rate_limit",
    "project_members",
    "users",
    "api_token",
    "capability_grant",
    "idempotency_key",
    "edge",
    "artifact",
    "decision",
    "run",
    "packet_version",
    "learning",
    "task",
    "project",
    "policy",
    "capability",
    "audit_log",
    "workspace",
    "actor",
]


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> Iterator[None]:
    with engine.begin() as connection:
        qualified_tables = ", ".join(
            f"agenticqueue.{table_name}" for table_name in TRUNCATE_TABLES
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


@pytest.fixture(autouse=True)
def admin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AQ_ADMIN_PASSCODE", "letmein-dev-passcode")
    monkeypatch.setenv("AQ_ADMIN_EMAIL", "admin@localhost")


def _bootstrap_body() -> dict[str, str]:
    return {
        "email": "admin@localhost",
        "passcode": "letmein-dev-passcode",
        "password": "CorrectHorse12!",
    }


def test_bootstrap_admin_replaces_legacy_setup_path(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    legacy_response = client.post("/setup")
    assert legacy_response.status_code == 401

    status_response = client.get("/api/auth/bootstrap_status")
    assert status_response.status_code == 200
    assert status_response.json() == {"needs_bootstrap": True}

    response = client.post("/api/auth/bootstrap_admin", json=_bootstrap_body())
    assert response.status_code == 200
    payload = response.json()
    assert payload["first_token"].startswith("aq_live_")

    with session_factory() as session:
        authenticated = authenticate_api_token(session, payload["first_token"])
        assert authenticated is not None
        assert authenticated.actor.actor_type == "admin"

    locked_response = client.post("/api/auth/bootstrap_admin", json=_bootstrap_body())
    assert locked_response.status_code == 409
