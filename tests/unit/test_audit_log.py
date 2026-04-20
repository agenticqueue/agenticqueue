from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api import audit as audit_module
from agenticqueue_api.app import create_app
from agenticqueue_api.audit import set_session_audit_context
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
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


def test_create_entity_writes_audit_row_with_actor_context(
    session_factory: sessionmaker[Session],
) -> None:
    auditor = seed_actor(
        session_factory,
        handle="audit-admin",
        actor_type="admin",
        display_name="Audit Admin",
    )

    with session_factory() as session:
        set_session_audit_context(
            session,
            actor_id=auditor.id,
            trace_id="trace-create",
        )
        workspace = WorkspaceRecord(slug="create-workspace", name="Create Workspace")
        session.add(workspace)
        session.commit()

        audit_row = latest_audit_row(
            session,
            entity_type="workspace",
            entity_id=workspace.id,
            action="CREATE",
        )

    assert audit_row.actor_id == auditor.id
    assert audit_row.trace_id == "trace-create"
    assert audit_row.before is None
    assert audit_row.after is not None
    assert audit_row.after["slug"] == "create-workspace"
    assert audit_row.after["id"] == str(workspace.id)


def test_update_entity_writes_before_and_after_snapshots(
    session_factory: sessionmaker[Session],
) -> None:
    auditor = seed_actor(
        session_factory,
        handle="update-admin",
        actor_type="admin",
        display_name="Update Admin",
    )

    with session_factory() as session:
        set_session_audit_context(
            session,
            actor_id=auditor.id,
            trace_id="trace-update",
        )
        workspace = WorkspaceRecord(slug="update-workspace", name="Update Workspace")
        session.add(workspace)
        session.commit()

        workspace.description = "Now with an audit trail"
        session.commit()

        audit_row = latest_audit_row(
            session,
            entity_type="workspace",
            entity_id=workspace.id,
            action="UPDATE",
        )

    assert audit_row.actor_id == auditor.id
    assert audit_row.before is not None
    assert audit_row.before["description"] is None
    assert audit_row.after is not None
    assert audit_row.after["description"] == "Now with an audit trail"


def test_delete_entity_writes_delete_audit_row(
    session_factory: sessionmaker[Session],
) -> None:
    auditor = seed_actor(
        session_factory,
        handle="delete-admin",
        actor_type="admin",
        display_name="Delete Admin",
    )

    with session_factory() as session:
        set_session_audit_context(
            session,
            actor_id=auditor.id,
            trace_id="trace-delete",
        )
        workspace = WorkspaceRecord(slug="delete-workspace", name="Delete Workspace")
        session.add(workspace)
        session.commit()

        workspace_id = workspace.id
        session.delete(workspace)
        session.commit()

        audit_row = latest_audit_row(
            session,
            entity_type="workspace",
            entity_id=workspace_id,
            action="DELETE",
        )

    assert audit_row.actor_id == auditor.id
    assert audit_row.before is not None
    assert audit_row.before["slug"] == "delete-workspace"
    assert audit_row.after is None


def test_direct_delete_on_audit_log_table_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    auditor = seed_actor(
        session_factory,
        handle="delete-guard-admin",
        actor_type="admin",
        display_name="Delete Guard Admin",
    )

    with session_factory() as session:
        set_session_audit_context(
            session,
            actor_id=auditor.id,
            trace_id="trace-delete-guard",
        )
        session.add(
            WorkspaceRecord(
                slug="guard-delete-workspace", name="Guard Delete Workspace"
            )
        )
        session.commit()

        with pytest.raises(sa.exc.DBAPIError, match="audit_log is append-only"):
            session.execute(sa.delete(AuditLogRecord))
            session.commit()
        session.rollback()


def test_direct_update_on_audit_log_table_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    auditor = seed_actor(
        session_factory,
        handle="update-guard-admin",
        actor_type="admin",
        display_name="Update Guard Admin",
    )

    with session_factory() as session:
        set_session_audit_context(
            session,
            actor_id=auditor.id,
            trace_id="trace-update-guard",
        )
        session.add(
            WorkspaceRecord(
                slug="guard-update-workspace", name="Guard Update Workspace"
            )
        )
        session.commit()

        with pytest.raises(sa.exc.DBAPIError, match="audit_log is append-only"):
            session.execute(sa.update(AuditLogRecord).values(action="MUTATED"))
            session.commit()
        session.rollback()


def test_trace_id_propagates_from_request_context(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    admin = seed_actor(
        session_factory,
        handle="trace-admin",
        actor_type="admin",
        display_name="Trace Admin",
    )
    target = seed_actor(
        session_factory,
        handle="trace-target",
        actor_type="agent",
        display_name="Trace Target",
    )

    with session_factory() as session:
        _, raw_token = issue_api_token(
            session,
            actor_id=admin.id,
            scopes=["admin:tokens"],
            expires_at=None,
        )
        session.commit()

    response = client.post(
        "/v1/auth/tokens",
        headers={
            "Authorization": f"Bearer {raw_token}",
            "X-Trace-Id": "trace-request-123",
        },
        json={
            "actor_id": str(target.id),
            "scopes": ["task:read"],
        },
    )

    assert response.status_code == 201
    created_token_id = uuid.UUID(response.json()["api_token"]["id"])

    with session_factory() as session:
        audit_row = latest_audit_row(
            session,
            entity_type="api_token",
            entity_id=created_token_id,
            action="CREATE",
        )

    assert audit_row.actor_id == admin.id
    assert audit_row.trace_id == "trace-request-123"


def test_audit_helpers_handle_none_and_non_dict_snapshots(
    session_factory: sessionmaker[Session],
) -> None:
    assert audit_module._serialize_snapshot(None) is None

    orphan_workspace = WorkspaceRecord(slug="orphan-workspace", name="Orphan Workspace")
    assert audit_module._pop_before_snapshot(orphan_workspace) is None

    with session_factory() as session:
        mapper = sa.inspect(WorkspaceRecord).mapper
        assert (
            audit_module._load_row_snapshot(session.connection(), mapper, None) is None
        )

        workspace = WorkspaceRecord(slug="helper-workspace", name="Helper Workspace")
        session.add(workspace)
        session.flush()

        session.info[audit_module._AUDIT_BEFORE_KEY] = "not-a-dict"
        assert audit_module._pop_before_snapshot(workspace) is None
        session.rollback()


def test_audit_helpers_skip_non_auditable_and_unmodified_targets(
    session_factory: sessionmaker[Session],
) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.info: dict[str, object] = {}
            self.dirty = [
                AuditLogRecord(entity_type="audit_log", action="CREATE"),
                WorkspaceRecord(slug="skip-workspace", name="Skip Workspace"),
            ]
            self.deleted = [
                AuditLogRecord(entity_type="audit_log", action="DELETE"),
            ]

        def connection(self) -> object:
            return object()

        def is_modified(
            self,
            target: object,
            include_collections: bool = False,
        ) -> bool:
            return False

    fake_session = FakeSession()
    audit_module._capture_before_snapshots(fake_session, None, None)
    assert fake_session.info[audit_module._AUDIT_BEFORE_KEY] == {}

    with session_factory() as session:
        before_count = session.scalar(
            sa.select(sa.func.count()).select_from(AuditLogRecord)
        )
        assert before_count is not None

        audit_module._write_audit_row(
            sa.inspect(AuditLogRecord).mapper,
            session.connection(),
            AuditLogRecord(entity_type="audit_log", action="CREATE"),
            action="CREATE",
            before=None,
            after=None,
        )

        after_count = session.scalar(
            sa.select(sa.func.count()).select_from(AuditLogRecord)
        )
        assert after_count == before_count
