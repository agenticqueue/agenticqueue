from __future__ import annotations

import io
import logging
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
import uvicorn

from agenticqueue_api.app import create_app
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.migrations import apply_database_migrations
from agenticqueue_api.routers import bootstrap as bootstrap_router

AUTH_TRUNCATE_TABLES = [
    "auth_audit_log",
    "auth_sessions",
    "auth_rate_limit",
    "project_members",
    "users",
    "api_token",
    "actor",
]
SERVER_START_ATTEMPTS = 100
SERVER_START_SLEEP_SECONDS = 0.05
SERVER_STOP_TIMEOUT_SECONDS = 5
HTTP_CLIENT_TIMEOUT_SECONDS = 5.0


@pytest.fixture(scope="session")
def engine() -> Engine:
    engine = sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)
    _truncate_auth_tables(engine)
    apply_database_migrations()
    return engine


@pytest.fixture(autouse=True)
def clean_auth_tables(engine: Engine) -> Iterator[None]:
    _truncate_auth_tables(engine)
    yield


def _truncate_auth_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        qualified_tables = ", ".join(
            f"agenticqueue.{table_name}" for table_name in AUTH_TRUNCATE_TABLES
        )
        connection.execute(
            sa.text(f"TRUNCATE TABLE {qualified_tables} RESTART IDENTITY CASCADE")
        )


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
    monkeypatch.setenv("AQ_ADMIN_EMAIL", "admin@localhost")


def _bootstrap_body(email: str = "admin@localhost") -> dict[str, str]:
    return {
        "email": email,
        "password": "CorrectHorse12!",
    }


def _seed_orphan_admin_actor(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        orphan_actor_insert = sa.text("""
                INSERT INTO agenticqueue.actor
                    (handle, actor_type, display_name, auth_subject, is_active)
                VALUES
                    ('admin', 'admin', 'Stale Admin',
                     'local:stale-admin@localhost', true)
                """)
        session.execute(orphan_actor_insert)


@contextmanager
def _serve_app(app: FastAPI) -> Iterator[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(SERVER_START_ATTEMPTS):
        if server.started:
            break
        time.sleep(SERVER_START_SLEEP_SECONDS)
    else:
        server.should_exit = True
        thread.join(timeout=SERVER_STOP_TIMEOUT_SECONDS)
        raise RuntimeError("Timed out waiting for uvicorn to start")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=SERVER_STOP_TIMEOUT_SECONDS)


def test_bootstrap_admin_concurrent_requests_allow_one_admin(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    def post_bootstrap(attempt: int) -> int:
        response = client.post(
            "/api/auth/bootstrap_admin",
            json=_bootstrap_body(email=f"admin{attempt}@localhost"),
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=20) as executor:
        statuses = list(executor.map(post_bootstrap, range(100)))

    assert statuses.count(200) == 1
    assert statuses.count(409) == 99

    with session_factory() as session:
        admin_count = session.scalar(
            sa.text("SELECT count(*) FROM agenticqueue.users WHERE is_admin = true")
        )

    assert admin_count == 1


def test_integrity_error_returns_409(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        actor_insert = sa.text("""
                INSERT INTO agenticqueue.actor
                    (handle, actor_type, display_name, auth_subject, is_active)
                VALUES
                    ('existing-admin', 'admin', 'Existing Admin',
                     'local:existing@localhost', true)
                RETURNING id
                """)
        actor_id = session.scalar(actor_insert)
        session.execute(
            sa.text("""
                INSERT INTO agenticqueue.users
                    (email, passcode_hash, actor_id, is_admin, is_active)
                VALUES
                    ('existing@localhost', 'hash', :actor_id, true, true)
                """),
            {"actor_id": actor_id},
        )

    monkeypatch.setattr(bootstrap_router, "_user_count", lambda session: 0)

    response = client.post(
        "/api/auth/bootstrap_admin",
        json=_bootstrap_body(email="new-admin@localhost"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert response.json()["message"] == "Bootstrap admin already exists"


def test_orphan_actor_returns_409(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _seed_orphan_admin_actor(session_factory)

    response = client.post(
        "/api/auth/bootstrap_admin",
        json=_bootstrap_body(email="new-admin@localhost"),
    )

    assert response.status_code == 409
    payload = response.json()
    assert "already exists" in payload["message"].lower()
    assert "uq_actor_handle" in str(payload["details"])


def test_response_parity_live_vs_testclient(
    session_factory: sessionmaker[Session],
) -> None:
    app = create_app(session_factory=session_factory)

    _seed_orphan_admin_actor(session_factory)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_response = test_client.post(
            "/api/auth/bootstrap_admin",
            json=_bootstrap_body(email="testclient-admin@localhost"),
        )

    _truncate_auth_tables(session_factory.kw["bind"])
    _seed_orphan_admin_actor(session_factory)
    with _serve_app(app) as base_url:
        live_response = httpx.post(
            f"{base_url}/api/auth/bootstrap_admin",
            json=_bootstrap_body(email="live-admin@localhost"),
            timeout=HTTP_CLIENT_TIMEOUT_SECONDS,
        )

    assert test_response.status_code == 409
    assert live_response.status_code == 409
    assert test_response.json() == live_response.json()


def test_409_logs_constraint_name(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _seed_orphan_admin_actor(session_factory)

    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setLevel(logging.WARNING)
    previous_level = bootstrap_router.logger.level
    bootstrap_router.logger.addHandler(handler)
    bootstrap_router.logger.setLevel(logging.WARNING)
    try:
        response = client.post(
            "/api/auth/bootstrap_admin",
            json=_bootstrap_body(email="new-admin@localhost"),
        )
    finally:
        bootstrap_router.logger.removeHandler(handler)
        bootstrap_router.logger.setLevel(previous_level)

    assert response.status_code == 409
    assert "uq_actor_handle" in log_stream.getvalue()
