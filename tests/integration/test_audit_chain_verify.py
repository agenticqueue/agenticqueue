from __future__ import annotations

import json
import sys
import time
import types
import uuid
from collections.abc import Iterator

import psycopg
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.types import UserDefinedType

if "pgvector.sqlalchemy" not in sys.modules:
    pgvector_module = types.ModuleType("pgvector")
    pgvector_sqlalchemy_module = types.ModuleType("pgvector.sqlalchemy")

    class Vector(UserDefinedType):
        cache_ok = True

        def __init__(self, dimensions: int) -> None:
            self.dimensions = dimensions

        def get_col_spec(self, **kw: object) -> str:
            return f"vector({self.dimensions})"

    setattr(pgvector_sqlalchemy_module, "Vector", Vector)
    setattr(pgvector_module, "sqlalchemy", pgvector_sqlalchemy_module)
    sys.modules["pgvector"] = pgvector_module
    sys.modules["pgvector.sqlalchemy"] = pgvector_sqlalchemy_module

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.config import (
    get_sqlalchemy_sync_database_url,
    get_sync_database_url,
)
from agenticqueue_api.models import ActorModel, CapabilityKey, CapabilityRecord
from agenticqueue_api.repo import create_actor

TRUNCATE_TABLES = [
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


def actor_id_for(handle: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{handle}")


def make_actor_payload(
    *,
    handle: str,
    actor_type: str,
    display_name: str,
) -> ActorModel:
    actor_id = actor_id_for(handle)
    payload = {
        "id": str(actor_id),
        "handle": handle,
        "actor_type": actor_type,
        "display_name": display_name,
        "auth_subject": f"{handle}-subject",
        "is_active": True,
        "created_at": "2026-04-20T00:00:00+00:00",
        "updated_at": "2026-04-20T00:00:00+00:00",
    }
    return ActorModel.model_validate_json(json.dumps(payload))


def truncate_all_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        qualified_tables = ", ".join(
            f"agenticqueue.{table_name}" for table_name in TRUNCATE_TABLES
        )
        connection.execute(
            sa.text(f"TRUNCATE TABLE {qualified_tables} RESTART IDENTITY CASCADE")
        )
        connection.execute(
            sa.insert(CapabilityRecord),
            [
                {
                    "key": capability,
                    "description": f"Seeded capability: {capability.value}",
                }
                for capability in CapabilityKey
            ],
        )


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> Iterator[None]:
    truncate_all_tables(engine)
    yield


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app(session_factory=session_factory)
    with TestClient(app) as test_client:
        yield test_client


def seed_actor(
    session_factory: sessionmaker[Session],
    *,
    handle: str,
    actor_type: str,
    display_name: str,
) -> ActorModel:
    with session_factory() as session:
        actor = create_actor(
            session,
            make_actor_payload(
                handle=handle,
                actor_type=actor_type,
                display_name=display_name,
            ),
        )
        session.commit()
        return actor


def issue_admin_token(
    session_factory: sessionmaker[Session],
    *,
    actor_id: uuid.UUID,
) -> str:
    with session_factory() as session:
        _, raw_token = issue_api_token(
            session,
            actor_id=actor_id,
            scopes=["admin:audit"],
            expires_at=None,
        )
        session.commit()
        return raw_token


def seed_audit_rows(count: int) -> None:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO agenticqueue.audit_log (
                  entity_type,
                  entity_id,
                  action,
                  before,
                  after,
                  trace_id
                )
                SELECT
                  'task',
                  gen_random_uuid(),
                  'CREATE',
                  NULL,
                  jsonb_build_object('ordinal', series),
                  'trace-' || series::text
                FROM generate_series(1, %s) AS series
                """,
                (count,),
            )
        connection.commit()


def clear_audit_log() -> None:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE TABLE agenticqueue.audit_log RESTART IDENTITY")
        connection.commit()


def fetch_verify_report(client: TestClient, token: str) -> dict[str, object]:
    response = client.get(
        "/audit/verify",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    return response.json()


def test_verify_endpoint_reports_healthy_chain(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    admin = seed_actor(
        session_factory,
        handle="verify-admin",
        actor_type="admin",
        display_name="Verify Admin",
    )
    token = issue_admin_token(session_factory, actor_id=admin.id)
    clear_audit_log()
    seed_audit_rows(3)

    report = fetch_verify_report(client, token)

    assert report == {
        "chain_length": 3,
        "verified_count": 3,
        "first_break_id_or_null": None,
    }


def test_verify_endpoint_finds_first_tampered_row(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    admin = seed_actor(
        session_factory,
        handle="tamper-admin",
        actor_type="admin",
        display_name="Tamper Admin",
    )
    token = issue_admin_token(session_factory, actor_id=admin.id)
    clear_audit_log()
    seed_audit_rows(5)

    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id
                FROM agenticqueue.audit_log
                ORDER BY chain_position
                OFFSET 2
                LIMIT 1
                """)
            row = cursor.fetchone()
            assert row is not None
            tampered_id = row[0]

            cursor.execute(
                "ALTER TABLE agenticqueue.audit_log "
                "DISABLE TRIGGER audit_log_append_only"
            )
            cursor.execute(
                """
                UPDATE agenticqueue.audit_log
                SET row_hash = decode(repeat('ff', 32), 'hex')
                WHERE id = %s
                """,
                (tampered_id,),
            )
            cursor.execute(
                "ALTER TABLE agenticqueue.audit_log "
                "ENABLE TRIGGER audit_log_append_only"
            )
        connection.commit()

    report = fetch_verify_report(client, token)

    assert report["chain_length"] == 5
    assert report["verified_count"] == 2
    assert report["first_break_id_or_null"] == str(tampered_id)


def test_verify_endpoint_checks_10000_row_chain_under_500ms(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    admin = seed_actor(
        session_factory,
        handle="perf-admin",
        actor_type="admin",
        display_name="Perf Admin",
    )
    token = issue_admin_token(session_factory, actor_id=admin.id)
    clear_audit_log()
    seed_audit_rows(10000)

    started = time.perf_counter()
    report = fetch_verify_report(client, token)
    duration_seconds = time.perf_counter() - started

    assert report == {
        "chain_length": 10000,
        "verified_count": 10000,
        "first_break_id_or_null": None,
    }
    assert duration_seconds < 0.5
