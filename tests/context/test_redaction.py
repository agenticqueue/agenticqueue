from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
import uuid

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.capabilities import grant_capability
from agenticqueue_api.compiler import compile_packet
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import (
    ActorModel,
    AuditLogRecord,
    CapabilityKey,
    CapabilityRecord,
    PolicyModel,
    ProjectModel,
    TaskModel,
    WorkspaceModel,
    WorkspaceRecord,
)
from agenticqueue_api.repo import (
    create_actor,
    create_policy,
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


def _actor_payload(*, handle: str) -> ActorModel:
    return ActorModel.model_validate(
        {
            "id": str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{handle}"
                )
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


def _fake_aws_access_key() -> str:
    return "AKIA" + "1234567890ABCDEF"


def _fake_github_pat() -> str:
    return "gh" + "p_" + "1234567890abcdef1234567890abcdef1234"


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


def _seed_task(
    session_factory: sessionmaker[Session],
    *,
    handle: str,
    spec: str,
    workspace_policy_body: dict[str, object] | None = None,
    grant_query_graph: bool = False,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, str | None]:
    contract = _example_contract()
    contract["spec"] = spec

    workspace_id = uuid.uuid4()
    project_id = uuid.uuid4()
    task_id = uuid.uuid4()
    actor_id = uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{handle}")

    with session_factory() as session:
        create_actor(session, _actor_payload(handle=handle))
        workspace_policy_id: uuid.UUID | None = uuid.uuid4()
        create_workspace(
            session,
            WorkspaceModel.model_validate(
                {
                    "id": str(workspace_id),
                    "policy_id": None,
                    "slug": f"{handle}-workspace",
                    "name": f"{handle.title()} Workspace",
                    "description": "Packet redaction tests",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        if workspace_policy_body is not None:
            create_policy(
                session,
                PolicyModel.model_validate(
                    {
                        "id": str(workspace_policy_id),
                        "workspace_id": str(workspace_id),
                        "name": f"{handle}-workspace-redaction",
                        "version": "1.0.0",
                        "hitl_required": True,
                        "autonomy_tier": 3,
                        "capabilities": ["read_repo", "write_branch", "run_tests"],
                        "body": workspace_policy_body,
                        "created_at": "2026-04-20T00:00:00+00:00",
                        "updated_at": "2026-04-20T00:00:00+00:00",
                    }
                ),
            )
            workspace_record = session.get(WorkspaceRecord, workspace_id)
            assert workspace_record is not None
            workspace_record.policy_id = workspace_policy_id
        else:
            workspace_policy_id = None

        create_project(
            session,
            ProjectModel.model_validate(
                {
                    "id": str(project_id),
                    "workspace_id": str(workspace_id),
                    "slug": f"{handle}-project",
                    "name": f"{handle.title()} Project",
                    "description": "Packet redaction tests",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        create_task(
            session,
            TaskModel.model_validate(
                {
                    "id": str(task_id),
                    "project_id": str(project_id),
                    "task_type": "coding-task",
                    "title": "Compile packet with redaction",
                    "state": "queued",
                    "description": "Packet redaction integration test.",
                    "contract": contract,
                    "definition_of_done": contract["dod_checklist"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        token: str | None = None
        if grant_query_graph:
            grant_capability(
                session,
                actor_id=actor_id,
                capability=CapabilityKey.QUERY_GRAPH,
                scope={"project_id": str(project_id)},
                granted_by_actor_id=actor_id,
            )
            _, token = issue_api_token(
                session,
                actor_id=actor_id,
                scopes=[],
                expires_at=None,
            )
        session.commit()

    return actor_id, project_id, task_id, token


def test_compile_packet_redacts_builtin_secret_and_tracks_count(
    session_factory: sessionmaker[Session],
) -> None:
    aws_key = _fake_aws_access_key()
    _, _, task_id, _ = _seed_task(
        session_factory,
        handle="packet-redaction-aws",
        spec=f"Use temporary credential {aws_key} while debugging.",
    )

    with session_factory() as session:
        packet = compile_packet(session, task_id)

        assert packet["redactions_count"] == 2
        serialized = json.dumps(packet)
        assert aws_key not in serialized
        assert "[REDACTED:aws_access_key]" in serialized
        assert session.info["agenticqueue_audit_redaction"] == {
            "redaction_count": 2,
            "source": "packet",
            "types_matched": ["aws_access_key"],
        }


def test_compile_packet_respects_workspace_custom_patterns(
    session_factory: sessionmaker[Session],
) -> None:
    custom_secret = "AQSECRET-ABC12345"
    _, _, task_id, _ = _seed_task(
        session_factory,
        handle="packet-redaction-custom",
        spec=f"Workspace-specific token {custom_secret} should never leave the packet.",
        workspace_policy_body={
            "packet_redaction_patterns": [
                {"kind": "workspace_secret", "pattern": r"AQSECRET-[A-Z0-9]{8}"}
            ]
        },
    )

    with session_factory() as session:
        packet = compile_packet(session, task_id)

    assert packet["redactions_count"] == 2
    serialized = json.dumps(packet)
    assert custom_secret not in serialized
    assert "[REDACTED:workspace_secret]" in serialized


def test_compile_packet_leaves_normal_prose_unredacted(
    session_factory: sessionmaker[Session],
) -> None:
    prose = "Keep the packet compiler output deterministic and readable."
    _, _, task_id, _ = _seed_task(
        session_factory,
        handle="packet-redaction-prose",
        spec=prose,
    )

    with session_factory() as session:
        packet = compile_packet(session, task_id)

    assert packet["redactions_count"] == 0
    assert prose in json.dumps(packet)


def test_packet_fetch_audits_packet_redaction_metadata(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    github_pat = _fake_github_pat()
    actor_id, project_id, task_id, token = _seed_task(
        session_factory,
        handle="packet-redaction-audit",
        spec=f"Reviewer note: repro with token {github_pat}.",
        grant_query_graph=True,
    )
    assert token is not None

    response = client.get(f"/tasks/{task_id}/packet", headers=_headers(token))

    assert response.status_code == 200
    body = response.json()
    assert body["task"]["project_id"] == str(project_id)
    assert body["redactions_count"] == 2
    assert github_pat not in json.dumps(body)

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
    assert rows[0].redaction == {
        "redaction_count": 2,
        "source": "packet",
        "types_matched": ["github_pat"],
    }
