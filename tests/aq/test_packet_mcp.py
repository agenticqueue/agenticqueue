from __future__ import annotations

import asyncio
import json
from pathlib import Path
import uuid

from fastapi.testclient import TestClient
from fastmcp import Client as FastMCPClient
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.capabilities import grant_capability
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.mcp import build_packets_mcp
from agenticqueue_api.models import (
    ActorModel,
    AuditLogRecord,
    CapabilityKey,
    CapabilityRecord,
    ProjectModel,
    TaskModel,
    WorkspaceModel,
)
from agenticqueue_api.packet_versions import get_current_packet_version, packet_content_hash
from agenticqueue_api.repo import (
    create_actor,
    create_project,
    create_task,
    create_workspace,
)
from agenticqueue_api.routers.packets import PACKET_FETCH_ACTION

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


def _actor_payload(*, handle: str, actor_type: str = "agent") -> ActorModel:
    return ActorModel.model_validate(
        {
            "id": str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{handle}"
                )
            ),
            "handle": handle,
            "actor_type": actor_type,
            "display_name": handle.replace("-", " ").title(),
            "auth_subject": f"{handle}-subject",
            "is_active": True,
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )


def _mcp_call(server, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
    async def _invoke() -> dict[str, object]:
        async with FastMCPClient(server) as client:
            result = await client.call_tool(tool_name, arguments)
            return result.data

    return asyncio.run(_invoke())


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
def client(session_factory: sessionmaker[Session]) -> TestClient:
    with TestClient(create_app(session_factory=session_factory)) as test_client:
        yield test_client


def _seed_task_with_token(
    session_factory: sessionmaker[Session],
    *,
    handle: str,
    actor_type: str = "agent",
    grant_query_graph: bool = False,
    grant_read_repo: bool = False,
    wrong_scope: bool = False,
    extra_capability: CapabilityKey | None = None,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, str]:
    contract = _example_contract()
    with session_factory() as session:
        actor = create_actor(
            session,
            _actor_payload(handle=handle, actor_type=actor_type),
        )
        workspace = create_workspace(
            session,
            WorkspaceModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "slug": f"{handle}-workspace",
                    "name": f"{handle.title()} Workspace",
                    "description": "Packet MCP tests",
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
                    "description": "Packet MCP tests",
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
                    "title": "Compile packet over MCP",
                    "state": "queued",
                    "description": "Render one packet from the MCP surface.",
                    "contract": contract,
                    "definition_of_done": contract["dod_checklist"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        capability_scope = (
            {"project_id": str(uuid.uuid4())}
            if wrong_scope
            else {"project_id": str(project.id)}
        )
        if grant_query_graph:
            grant_capability(
                session,
                actor_id=actor.id,
                capability=CapabilityKey.QUERY_GRAPH,
                scope=capability_scope,
                granted_by_actor_id=actor.id,
            )
        if grant_read_repo:
            grant_capability(
                session,
                actor_id=actor.id,
                capability=CapabilityKey.READ_REPO,
                scope=capability_scope,
                granted_by_actor_id=actor.id,
            )
        if extra_capability is not None:
            grant_capability(
                session,
                actor_id=actor.id,
                capability=extra_capability,
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


def test_compile_packet_mcp_matches_rest_and_records_audit(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    actor_id, project_id, task_id, token = _seed_task_with_token(
        session_factory,
        handle="packet-mcp-parity",
        grant_query_graph=True,
    )
    mcp = build_packets_mcp()

    rest_response = client.get(f"/tasks/{task_id}/packet", headers=_headers(token))
    mcp_response = _mcp_call(
        mcp,
        "compile_packet",
        {"task_id": str(task_id), "token": token},
    )

    assert rest_response.status_code == 200
    assert mcp_response == rest_response.json()
    assert packet_content_hash(mcp_response) == packet_content_hash(rest_response.json())

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

    assert len(rows) == 2
    assert [row.actor_id for row in rows] == [actor_id, actor_id]
    assert rows[-1].after == {
        "packet_version_id": mcp_response["packet_version_id"],
        "project_id": str(project_id),
        "retrieval_tiers_used": ["graph", "surface"],
    }


def test_compile_packet_mcp_accepts_read_repo_capability(
    session_factory: sessionmaker[Session],
) -> None:
    _, _, task_id, token = _seed_task_with_token(
        session_factory,
        handle="packet-mcp-read-repo",
        grant_read_repo=True,
    )
    mcp = build_packets_mcp(session_factory=session_factory)

    response = _mcp_call(
        mcp,
        "compile_packet",
        {"task_id": str(task_id), "token": token},
    )

    with session_factory() as session:
        packet_version = get_current_packet_version(session, task_id)

    assert response["task"]["id"] == str(task_id)
    assert packet_version is not None
    assert response["packet_version_id"] == str(packet_version.id)
    assert packet_content_hash(response) == packet_version.packet_hash


def test_compile_packet_mcp_accepts_admin_actor_without_explicit_grants(
    session_factory: sessionmaker[Session],
) -> None:
    _, _, task_id, token = _seed_task_with_token(
        session_factory,
        handle="packet-mcp-admin",
        actor_type="admin",
    )
    mcp = build_packets_mcp(session_factory=session_factory)

    response = _mcp_call(
        mcp,
        "compile_packet",
        {"task_id": str(task_id), "token": token},
    )

    assert response["task"]["id"] == str(task_id)
    assert response["retrieval_tiers_used"] == ["graph", "surface"]


def test_compile_packet_mcp_requires_authentication(
    session_factory: sessionmaker[Session],
) -> None:
    _, _, task_id, _ = _seed_task_with_token(
        session_factory,
        handle="packet-mcp-auth",
        grant_query_graph=True,
    )
    mcp = build_packets_mcp(session_factory=session_factory)

    response = _mcp_call(mcp, "compile_packet", {"task_id": str(task_id)})

    assert response == {
        "error_code": "unauthorized",
        "message": "Missing Authorization header",
        "details": None,
    }


def test_compile_packet_mcp_rejects_invalid_token(
    session_factory: sessionmaker[Session],
) -> None:
    _, _, task_id, _ = _seed_task_with_token(
        session_factory,
        handle="packet-mcp-invalid-token",
        grant_query_graph=True,
    )
    mcp = build_packets_mcp(session_factory=session_factory)

    response = _mcp_call(
        mcp,
        "compile_packet",
        {"task_id": str(task_id), "token": "not-a-real-token"},
    )

    assert response == {
        "error_code": "unauthorized",
        "message": "Invalid bearer token",
        "details": None,
    }


def test_compile_packet_mcp_rejects_missing_capability_with_audit(
    session_factory: sessionmaker[Session],
) -> None:
    actor_id, project_id, task_id, token = _seed_task_with_token(
        session_factory,
        handle="packet-mcp-missing-capability",
        grant_read_repo=True,
        wrong_scope=True,
        extra_capability=CapabilityKey.UPDATE_TASK,
    )
    mcp = build_packets_mcp(session_factory=session_factory)

    response = _mcp_call(
        mcp,
        "compile_packet",
        {"task_id": str(task_id), "token": token},
    )

    assert response == {
        "error_code": "forbidden",
        "message": "Capability grant required",
        "details": {
            "missing_capabilities": ["read_repo", "query_graph"],
            "required_scope": {"project_id": str(project_id)},
        },
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
    assert rows[0].after == response["details"]


def test_compile_packet_mcp_returns_404_for_missing_task(
    session_factory: sessionmaker[Session],
) -> None:
    _, _, _, token = _seed_task_with_token(
        session_factory,
        handle="packet-mcp-missing-task",
        grant_query_graph=True,
    )
    mcp = build_packets_mcp(session_factory=session_factory)

    response = _mcp_call(
        mcp,
        "compile_packet",
        {"task_id": str(uuid.uuid4()), "token": token},
    )

    assert response == {
        "error_code": "not_found",
        "message": "Task not found",
        "details": None,
    }
