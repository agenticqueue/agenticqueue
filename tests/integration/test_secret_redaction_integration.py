from __future__ import annotations

import json
from pathlib import Path
import uuid

import pytest
import sqlalchemy as sa
import yaml
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
    TaskRecord,
)
from agenticqueue_api.models.project import ProjectModel
from agenticqueue_api.models.workspace import WorkspaceModel
from agenticqueue_api.repo import create_actor, create_project, create_workspace

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


def _fake_aws_access_key() -> str:
    return "AKIA" + "1234567890ABCDEF"


def _fake_github_pat() -> str:
    return "gh" + "p_" + "1234567890abcdef1234567890abcdef1234"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _example_contract() -> dict[str, object]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _policy_dir(tmp_path: Path, *, hard_block_secrets: bool) -> Path:
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    (policy_dir / "default-coding.policy.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "1.0.0",
                "hitl_required": True,
                "autonomy_tier": 3,
                "capabilities": ["read_repo", "write_branch"],
                "body": {"hard_block_secrets": hard_block_secrets},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return policy_dir


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


def make_actor_payload(*, handle: str, actor_type: str) -> ActorModel:
    return ActorModel.model_validate(
        {
            "id": str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"https://agenticqueue.ai/tests/{handle}",
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


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> None:
    truncate_all_tables(engine)


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def seed_admin(session_factory: sessionmaker[Session]) -> tuple[ActorModel, str]:
    with session_factory() as session:
        admin = create_actor(
            session,
            make_actor_payload(handle="secret-redaction-admin", actor_type="admin"),
        )
        _, raw_token = issue_api_token(
            session,
            actor_id=admin.id,
            scopes=["task:read", "task:write"],
            expires_at=None,
        )
        session.commit()
        return admin, raw_token


def seed_project(session_factory: sessionmaker[Session]) -> uuid.UUID:
    with session_factory() as session:
        workspace = create_workspace(
            session,
            WorkspaceModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "slug": "secret-redaction-workspace",
                    "name": "Secret Redaction Workspace",
                    "description": "Workspace for secret redaction tests",
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
                    "slug": "secret-redaction-project",
                    "name": "Secret Redaction Project",
                    "description": "Project for secret redaction tests",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        session.commit()
        return project.id


def task_payload(project_id: uuid.UUID) -> dict[str, object]:
    contract = _example_contract()
    contract["spec"] = (
        "Rotate [REDACTED] instead of using " + _fake_github_pat() + "."
    )
    return {
        "id": str(uuid.uuid4()),
        "project_id": str(project_id),
        "task_type": "coding-task",
        "title": "Secret redaction task create",
        "state": "queued",
        "description": f"Do not persist {_fake_aws_access_key()} in audit rows.",
        "contract": contract,
        "definition_of_done": contract["dod_checklist"],
        "created_at": "2026-04-20T00:00:00+00:00",
        "updated_at": "2026-04-20T00:00:00+00:00",
    }


def latest_audit_row(session: Session, *, entity_id: uuid.UUID) -> AuditLogRecord:
    statement = (
        sa.select(AuditLogRecord)
        .where(
            AuditLogRecord.entity_type == "task",
            AuditLogRecord.entity_id == entity_id,
            AuditLogRecord.action == "CREATE",
        )
        .order_by(AuditLogRecord.created_at.desc(), AuditLogRecord.id.desc())
    )
    row = session.scalars(statement).first()
    assert row is not None
    return row


def test_secret_redaction_redacts_task_payload_before_audit_persistence(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    admin, token = seed_admin(session_factory)
    project_id = seed_project(session_factory)
    with session_factory() as session:
        grant_capability(
            session,
            actor_id=admin.id,
            capability=CapabilityKey.WRITE_BRANCH,
            scope={"project_id": str(project_id)},
            granted_by_actor_id=admin.id,
        )
        session.commit()

    app = create_app(
        session_factory=session_factory,
        policies_dir=_policy_dir(tmp_path, hard_block_secrets=False),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/tasks",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": str(uuid.uuid4()),
            },
            json=task_payload(project_id),
        )

    assert response.status_code == 201
    task_id = uuid.UUID(response.json()["id"])

    with session_factory() as session:
        created_task = session.get(TaskRecord, task_id)
        assert created_task is not None
        assert _fake_aws_access_key() not in created_task.description
        assert "[REDACTED:aws_access_key]" in created_task.description
        assert _fake_github_pat() not in json.dumps(created_task.contract)
        assert "[REDACTED:github_pat]" in json.dumps(created_task.contract)

        audit_row = latest_audit_row(session, entity_id=task_id)
        assert audit_row.after is not None
        assert _fake_aws_access_key() not in json.dumps(audit_row.after)
        assert _fake_github_pat() not in json.dumps(audit_row.after)
        assert audit_row.redaction is not None
        assert audit_row.redaction["redaction_count"] == 2
        assert audit_row.redaction["types_matched"] == [
            "github_pat",
            "aws_access_key",
        ] or audit_row.redaction["types_matched"] == [
            "aws_access_key",
            "github_pat",
        ]
        assert len(audit_row.redaction["original_sha256"]) == 64
