from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.capabilities import grant_capability
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import (
    ActorModel,
    AuditLogRecord,
    CapabilityKey,
    CapabilityRecord,
    ProjectModel,
    TaskModel,
    WorkspaceModel,
)
from agenticqueue_api.repo import create_actor, create_project, create_task, create_workspace
from agenticqueue_api.routers.packets import (
    PACKET_FETCH_ACTION,
    PACKET_FETCH_CACHE_CONTROL,
)

TRUNCATE_TABLES = [
    "api_token",
    "capability_grant",
    "idempotency_key",
    "edge",
    "artifact",
    "decision",
    "run",
    "packet_version",
    "learning_drafts",
    "learning",
    "task",
    "project",
    "policy",
    "capability",
    "audit_log",
    "workspace",
    "actor",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _example_contract() -> dict[str, object]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _truncate_all_tables(engine: Engine) -> None:
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


def _actor_payload(*, handle: str) -> ActorModel:
    return ActorModel.model_validate(
        {
            "id": str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{handle}")
            ),
            "handle": handle,
            "actor_type": "agent",
            "display_name": handle.replace("-", " ").title(),
            "auth_subject": f"{handle}-subject",
            "is_active": True,
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> None:
    _truncate_all_tables(engine)


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(create_app(session_factory=session_factory)) as test_client:
        yield test_client


def _seed_task_with_token(
    session_factory: sessionmaker[Session],
    *,
    handle: str,
    grant_query_graph: bool,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, str]:
    contract = _example_contract()
    with session_factory() as session:
        actor = create_actor(session, _actor_payload(handle=handle))
        workspace = create_workspace(
            session,
            WorkspaceModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "slug": f"{handle}-workspace",
                    "name": f"{handle.title()} Workspace",
                    "description": "Packet REST tests",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        project = create_project(
            session,
            ProjectModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "workspace_id": str(workspace.id),
                    "slug": f"{handle}-project",
                    "name": f"{handle.title()} Project",
                    "description": "Packet REST tests",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        task = create_task(
            session,
            TaskModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": "Fetch packet over REST",
                    "state": "queued",
                    "description": "Compile one packet over HTTP.",
                    "contract": contract,
                    "definition_of_done": contract["dod_checklist"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        if grant_query_graph:
            grant_capability(
                session,
                actor_id=actor.id,
                capability=CapabilityKey.QUERY_GRAPH,
                scope={"project_id": str(project.id)},
                granted_by_actor_id=actor.id,
            )
        _, token = issue_api_token(
            session,
            actor_id=actor.id,
            scopes=[],
            expires_at=None,
        )
        session.commit()
        return actor.id, project.id, task.id, token


def test_get_task_packet_returns_packet_headers_and_fetch_audit(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    actor_id, project_id, task_id, token = _seed_task_with_token(
        session_factory,
        handle="packet-rest-success",
        grant_query_graph=True,
    )

    response = client.get(f"/tasks/{task_id}/packet", headers=_headers(token))

    assert response.status_code == 200
    body = response.json()
    assert body["task"]["id"] == str(task_id)
    assert body["task"]["project_id"] == str(project_id)
    assert body["task_contract"]["repo"] == "github.com/agenticqueue/agenticqueue"
    assert response.headers["X-Packet-Version"] == body["packet_version_id"]
    assert response.headers["Cache-Control"] == PACKET_FETCH_CACHE_CONTROL
    assert body["retrieval_tiers_used"] == ["graph", "surface"]

    with session_factory() as session:
        rows = session.scalars(
            sa.select(AuditLogRecord)
            .where(
                AuditLogRecord.entity_type == "task",
                AuditLogRecord.entity_id == task_id,
                AuditLogRecord.action == PACKET_FETCH_ACTION,
            )
            .order_by(AuditLogRecord.created_at.asc(), AuditLogRecord.id.asc())
        ).all()

    assert len(rows) == 1
    assert rows[0].actor_id == actor_id
    assert rows[0].after == {
        "packet_version_id": body["packet_version_id"],
        "project_id": str(project_id),
        "retrieval_tiers_used": ["graph", "surface"],
    }


def test_get_task_packet_requires_authentication(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _, _, task_id, _ = _seed_task_with_token(
        session_factory,
        handle="packet-rest-auth",
        grant_query_graph=True,
    )

    response = client.get(f"/tasks/{task_id}/packet")

    assert response.status_code == 401
    assert response.json()["message"] == "Missing Authorization header"


def test_get_task_packet_requires_query_graph_capability(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    actor_id, project_id, task_id, token = _seed_task_with_token(
        session_factory,
        handle="packet-rest-capability",
        grant_query_graph=False,
    )

    response = client.get(f"/tasks/{task_id}/packet", headers=_headers(token))

    assert response.status_code == 403
    assert response.json()["details"] == {
        "missing_capability": "query_graph",
        "required_scope": {"project_id": str(project_id)},
    }

    with session_factory() as session:
        rows = session.scalars(
            sa.select(AuditLogRecord)
            .where(
                AuditLogRecord.entity_type == "task",
                AuditLogRecord.entity_id == task_id,
                AuditLogRecord.action == "CAPABILITY_DENIED",
            )
            .order_by(AuditLogRecord.created_at.asc(), AuditLogRecord.id.asc())
        ).all()

    assert len(rows) == 1
    assert rows[0].actor_id == actor_id
    assert rows[0].after == {
        "missing_capability": "query_graph",
        "required_scope": {"project_id": str(project_id)},
    }


def test_get_task_packet_returns_404_for_missing_task(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _, _, _, token = _seed_task_with_token(
        session_factory,
        handle="packet-rest-missing",
        grant_query_graph=True,
    )

    response = client.get(f"/tasks/{uuid.uuid4()}/packet", headers=_headers(token))

    assert response.status_code == 404
    assert response.json()["message"] == "Task not found"
