from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.local_auth import ensure_admin_seed

AUTH_TRUNCATE_TABLES = [
    "auth_audit_log",
    "auth_sessions",
    "auth_rate_limit",
    "project_members",
    "users",
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


def test_ensure_admin_seed_uses_admin_email_fallback(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: sessionmaker[Session],
) -> None:
    monkeypatch.setenv("AQ_ADMIN_PASSCODE", "letmein-dev-passcode")
    monkeypatch.delenv("AQ_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("AGENTICQUEUE_ADMIN_EMAIL", raising=False)

    with session_factory() as session:
        ensure_admin_seed(session)
        session.commit()

    with session_factory() as session:
        row = session.execute(sa.text("""
                SELECT email, is_admin
                FROM agenticqueue.users
                """)).one()

    assert row.email == "admin@localhost"
    assert row.is_admin is True
