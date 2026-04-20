from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.capabilities import grant_capability
from agenticqueue_api.cli import app as cli_app
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.middleware.idempotency import IDEMPOTENCY_KEY_HEADER
from agenticqueue_api.models import ActorModel, CapabilityKey, CapabilityRecord, TaskRecord
from agenticqueue_api.models.idempotency_key import IdempotencyKeyRecord
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

runner = CliRunner()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def example_contract() -> dict[str, object]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    return json.loads(path.read_text(encoding="utf-8"))


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


def seed_admin(session_factory: sessionmaker[Session]) -> tuple[ActorModel, str]:
    with session_factory() as session:
        admin = create_actor(
            session,
            make_actor_payload(handle="idempotency-admin", actor_type="admin"),
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
                    "slug": "idempotency-workspace",
                    "name": "Idempotency Workspace",
                    "description": "Workspace for replay tests",
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
                    "slug": "idempotency-project",
                    "name": "Idempotency Project",
                    "description": "Project for replay tests",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        session.commit()
        return project.id


def task_payload(project_id: uuid.UUID) -> dict[str, object]:
    contract = example_contract()
    return {
        "id": str(uuid.uuid4()),
        "project_id": str(project_id),
        "task_type": "coding-task",
        "title": "Replay-safe task create",
        "state": "queued",
        "description": "Create one task once, replay the response nine times.",
        "contract": contract,
        "definition_of_done": contract["dod_checklist"],
        "created_at": "2026-04-20T00:00:00+00:00",
        "updated_at": "2026-04-20T00:00:00+00:00",
    }


def test_same_key_same_payload_creates_one_task_and_cli_stats_report_hits(
    client: TestClient,
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

    payload = task_payload(project_id)
    key = str(uuid.uuid4())
    headers = {
        "Authorization": f"Bearer {token}",
        IDEMPOTENCY_KEY_HEADER: key,
    }

    statuses: list[int] = []
    for _ in range(10):
        response = client.post("/v1/tasks", headers=headers, json=payload)
        statuses.append(response.status_code)

    assert statuses == [201] + [200] * 9

    with session_factory() as session:
        task_count = int(
            session.scalar(sa.select(sa.func.count()).select_from(TaskRecord)) or 0
        )
        assert task_count == 1

        record = session.get(IdempotencyKeyRecord, key)
        assert record is not None
        assert record.replay_count == 9
        record.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
        session.commit()

    stats_result = runner.invoke(cli_app, ["idempotency", "stats"])
    assert stats_result.exit_code == 0
    stats = json.loads(stats_result.stdout)
    assert stats["hit_count"] == 9
    assert stats["miss_count"] == 1
    assert stats["expired_count"] == 1
    assert stats["active_count"] == 0

    cleanup_result = runner.invoke(cli_app, ["idempotency", "cleanup"])
    assert cleanup_result.exit_code == 0
    assert json.loads(cleanup_result.stdout) == {"deleted": 1}
