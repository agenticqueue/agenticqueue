from __future__ import annotations

import hashlib
import hmac
import statistics
import time
import re
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import authenticate_api_token
from agenticqueue_api.config import get_sqlalchemy_sync_database_url

AUTH_TRUNCATE_TABLES = [
    "auth_audit_log",
    "auth_sessions",
    "auth_rate_limit",
    "project_members",
    "users",
    "api_token",
    "actor",
]


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


@pytest.fixture(autouse=True)
def admin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AQ_ADMIN_PASSCODE", "letmein-dev-passcode")
    monkeypatch.setenv("AQ_ADMIN_EMAIL", "admin@localhost")


def _bootstrap_body(passcode: str = "letmein-dev-passcode") -> dict[str, str]:
    return {
        "email": "ADMIN@LOCALHOST",
        "passcode": passcode,
        "password": "CorrectHorse12!",
    }


def test_happy_path(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    status_response = client.get("/api/auth/bootstrap_status")
    assert status_response.status_code == 200
    assert status_response.json() == {"needs_bootstrap": True}

    response = client.post("/api/auth/bootstrap_admin", json=_bootstrap_body())

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert "aq_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie

    payload = response.json()
    assert payload["user"]["email"] == "admin@localhost"
    assert payload["user"]["role"] == "owner"
    assert re.fullmatch(r"aq_live_[A-Za-z0-9]{32,}", payload["first_token"])

    locked_response = client.get("/api/auth/bootstrap_status")
    assert locked_response.status_code == 200
    assert locked_response.json() == {"needs_bootstrap": False}

    with session_factory() as session:
        authenticated = authenticate_api_token(session, payload["first_token"])
        assert authenticated is not None
        assert authenticated.actor.actor_type == "admin"


def test_wrong_passcode_constant_time(client: TestClient) -> None:
    wrong_response = client.post(
        "/api/auth/bootstrap_admin",
        json=_bootstrap_body(passcode="wrong-passcode"),
    )
    assert wrong_response.status_code == 401

    expected = hashlib.sha256(b"letmein-dev-passcode").digest()

    def compare(candidate: str) -> bool:
        candidate_digest = hashlib.sha256(candidate.encode("utf-8")).digest()
        return hmac.compare_digest(candidate_digest, expected)

    short_wrong_samples = []
    long_wrong_samples = []
    for _ in range(20):
        start = time.perf_counter_ns()
        compare("x")
        short_wrong_samples.append(time.perf_counter_ns() - start)

        start = time.perf_counter_ns()
        compare("wrong-passcode-with-a-much-longer-length")
        long_wrong_samples.append(time.perf_counter_ns() - start)

    median_diff_ms = (
        abs(
            statistics.median(short_wrong_samples)
            - statistics.median(long_wrong_samples)
        )
        / 1_000_000
    )
    assert median_diff_ms < 5


def test_env_unset(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.delenv("AQ_ADMIN_PASSCODE", raising=False)
    monkeypatch.delenv("AGENTICQUEUE_ADMIN_PASSCODE", raising=False)

    response = client.post("/api/auth/bootstrap_admin", json=_bootstrap_body())

    assert response.status_code == 503
    assert "AQ_ADMIN_PASSCODE" in response.json()["message"]


def test_locked_after_user_one(client: TestClient) -> None:
    first = client.post("/api/auth/bootstrap_admin", json=_bootstrap_body())
    assert first.status_code == 200

    status_response = client.get("/api/auth/bootstrap_status")
    assert status_response.status_code == 200
    assert status_response.json() == {"needs_bootstrap": False}

    second = client.post("/api/auth/bootstrap_admin", json=_bootstrap_body())
    assert second.status_code == 404


def test_token_hash_only(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    response = client.post("/api/auth/bootstrap_admin", json=_bootstrap_body())
    assert response.status_code == 200
    first_token = response.json()["first_token"]

    with session_factory() as session:
        token_hash_query = sa.text("""
                SELECT token_hash
                FROM agenticqueue.api_token
                """)
        stored_token = session.execute(token_hash_query).scalar_one()

    assert stored_token != first_token
    assert first_token not in stored_token
