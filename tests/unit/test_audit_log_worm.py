from __future__ import annotations

import json
import sys
import types
import uuid
from collections.abc import Iterator

import psycopg
import pytest
import sqlalchemy as sa
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

from agenticqueue_api.audit import set_session_audit_context
from agenticqueue_api.config import (
    get_sqlalchemy_sync_database_url,
    get_sync_database_url,
)
from agenticqueue_api.models import (
    ActorModel,
    AuditLogRecord,
    CapabilityKey,
    CapabilityRecord,
)
from agenticqueue_api.models.workspace import WorkspaceRecord
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
ZERO_HASH = b"\x00" * 32


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


def latest_audit_row(
    session: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
) -> AuditLogRecord:
    statement = (
        sa.select(AuditLogRecord)
        .where(
            AuditLogRecord.entity_type == entity_type,
            AuditLogRecord.entity_id == entity_id,
            AuditLogRecord.action == action,
        )
        .order_by(AuditLogRecord.created_at.desc(), AuditLogRecord.id.desc())
    )
    row = session.scalars(statement).first()
    assert row is not None
    return row


def test_audit_log_rows_link_prev_hash_and_row_hash(
    session_factory: sessionmaker[Session],
) -> None:
    auditor = seed_actor(
        session_factory,
        handle="worm-admin",
        actor_type="admin",
        display_name="Worm Admin",
    )

    with session_factory() as session:
        set_session_audit_context(
            session,
            actor_id=auditor.id,
            trace_id="trace-worm-1",
        )
        first_workspace = WorkspaceRecord(slug="worm-one", name="Worm One")
        session.add(first_workspace)
        session.commit()
        set_session_audit_context(
            session,
            actor_id=auditor.id,
            trace_id="trace-worm-2",
        )
        second_workspace = WorkspaceRecord(slug="worm-two", name="Worm Two")
        session.add(second_workspace)
        session.commit()

        chain_rows = list(
            session.scalars(
                sa.select(AuditLogRecord).order_by(
                    AuditLogRecord.chain_position.asc(),
                )
            )
        )

    assert len(chain_rows) >= 3
    assert chain_rows[0].prev_hash == ZERO_HASH
    assert len(chain_rows[0].row_hash) == 32
    for previous_row, current_row in zip(chain_rows, chain_rows[1:]):
        assert current_row.prev_hash == previous_row.row_hash
        assert len(current_row.row_hash) == 32


def test_superuser_update_and_delete_are_rejected_with_append_only_sqlstate(
    session_factory: sessionmaker[Session],
) -> None:
    auditor = seed_actor(
        session_factory,
        handle="worm-guard-admin",
        actor_type="admin",
        display_name="Worm Guard Admin",
    )

    with session_factory() as session:
        set_session_audit_context(
            session,
            actor_id=auditor.id,
            trace_id="trace-worm-guard",
        )
        session.add(WorkspaceRecord(slug="worm-guard", name="Worm Guard"))
        session.commit()

        with pytest.raises(sa.exc.DBAPIError) as update_exc:
            session.execute(sa.update(AuditLogRecord).values(action="MUTATED"))
            session.commit()
        session.rollback()

        with pytest.raises(sa.exc.DBAPIError) as delete_exc:
            session.execute(sa.delete(AuditLogRecord))
            session.commit()
        session.rollback()

    assert getattr(update_exc.value.orig, "sqlstate", None) == "55000"
    assert getattr(delete_exc.value.orig, "sqlstate", None) == "55000"


def test_agenticqueue_app_role_cannot_update_or_delete_audit_rows(
    session_factory: sessionmaker[Session],
) -> None:
    auditor = seed_actor(
        session_factory,
        handle="worm-role-admin",
        actor_type="admin",
        display_name="Worm Role Admin",
    )

    with session_factory() as session:
        set_session_audit_context(
            session,
            actor_id=auditor.id,
            trace_id="trace-role-guard",
        )
        session.add(WorkspaceRecord(slug="worm-role", name="Worm Role"))
        session.commit()

    with psycopg.connect(get_sync_database_url(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET ROLE agenticqueue_app")

            with pytest.raises(psycopg.errors.InsufficientPrivilege) as update_exc:
                cursor.execute("UPDATE agenticqueue.audit_log SET action = 'MUTATED'")

            with pytest.raises(psycopg.errors.InsufficientPrivilege) as delete_exc:
                cursor.execute("DELETE FROM agenticqueue.audit_log")

            cursor.execute("RESET ROLE")

    assert update_exc.value.sqlstate == "42501"
    assert delete_exc.value.sqlstate == "42501"
