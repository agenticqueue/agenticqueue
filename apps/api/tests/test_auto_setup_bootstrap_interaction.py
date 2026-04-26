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
from agenticqueue_api.local_auth import hash_password
from agenticqueue_api.migrations import apply_database_migrations
from agenticqueue_api.models import ActorRecord, UserRecord

AUTH_TRUNCATE_TABLES = [
    "auth_audit_log",
    "auth_sessions",
    "auth_rate_limit",
    "project_members",
    "users",
    "api_token",
    "actor",
]
DEMO_EMAIL = "admin@localhost"
DEMO_PASSWORD = "DemoAdmin12!"
REAL_EMAIL = "real-admin@localhost"
REAL_PASSWORD = "CorrectHorse12!"


@pytest.fixture(scope="session")
def engine() -> Engine:
    engine = sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)
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


def _bootstrap_body(email: str = REAL_EMAIL) -> dict[str, str]:
    return {
        "email": email,
        "password": REAL_PASSWORD,
    }


def _seed_demo_admin(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        actor = ActorRecord(
            handle="admin",
            actor_type="admin",
            display_name="Admin",
            auth_subject=f"local:{DEMO_EMAIL}",
            is_active=True,
        )
        session.add(actor)
        session.flush()
        user = UserRecord(
            email=DEMO_EMAIL,
            passcode_hash=hash_password(DEMO_PASSWORD),
            actor_id=actor.id,
            is_admin=True,
            is_active=True,
        )
        session.add(user)


def _delete_demo_user_only(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        session.execute(
            sa.delete(UserRecord).where(UserRecord.email == DEMO_EMAIL),
        )


def test_manual_delete_then_real_bootstrap(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _seed_demo_admin(session_factory)
    _delete_demo_user_only(session_factory)

    response = client.post("/api/auth/bootstrap_admin", json=_bootstrap_body())

    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["email"] == REAL_EMAIL
    assert payload["first_token"].startswith("aq_live_")

    with session_factory() as session:
        active_admin = session.scalar(
            sa.select(ActorRecord).where(ActorRecord.handle == "admin")
        )
        assert active_admin is not None
        assert active_admin.auth_subject == f"local:{REAL_EMAIL}"
        assert active_admin.is_active is True

        archived_admin = session.scalar(
            sa.select(ActorRecord).where(ActorRecord.handle.like("admin-archived-%"))
        )
        assert archived_admin is not None
        assert archived_admin.auth_subject == f"local:{DEMO_EMAIL}"
        assert archived_admin.is_active is False

        real_user = session.scalar(
            sa.select(UserRecord).where(UserRecord.email == REAL_EMAIL)
        )
        assert real_user is not None
        assert real_user.actor_id == active_admin.id

        authenticated = authenticate_api_token(session, payload["first_token"])
        assert authenticated is not None
        assert authenticated.actor.id == active_admin.id


def test_fresh_demo_path(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _seed_demo_admin(session_factory)

    status_response = client.get("/api/auth/bootstrap_status")
    session_response = client.post(
        "/api/session",
        json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD},
    )
    second_bootstrap = client.post(
        "/api/auth/bootstrap_admin",
        json=_bootstrap_body(email="another-admin@localhost"),
    )

    assert status_response.status_code == 200
    assert status_response.json() == {"needs_bootstrap": False}
    assert session_response.status_code == 200
    assert session_response.json()["user"] == {
        "email": DEMO_EMAIL,
        "is_admin": True,
    }
    assert second_bootstrap.status_code == 409
