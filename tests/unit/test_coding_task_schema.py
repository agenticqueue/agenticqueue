from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from jsonschema import ValidationError  # type: ignore[import-untyped]
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.config import get_sqlalchemy_sync_database_url, get_task_types_dir
from agenticqueue_api.models import ActorModel, CapabilityKey, CapabilityRecord
from agenticqueue_api.models.project import ProjectModel
from agenticqueue_api.models.workspace import WorkspaceModel
from agenticqueue_api.repo import create_actor, create_project, create_workspace
from agenticqueue_api.task_type_registry import SchemaLoadError, TaskTypeRegistry

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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _example_contract() -> dict[str, Any]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_capabilities(engine: Engine) -> None:
    with engine.begin() as connection:
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


def _truncate_all_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        qualified_tables = ", ".join(
            f"agenticqueue.{table_name}" for table_name in TRUNCATE_TABLES
        )
        connection.execute(
            sa.text(f"TRUNCATE TABLE {qualified_tables} RESTART IDENTITY CASCADE")
        )
    _seed_capabilities(engine)


def _seed_actor(
    session_factory: sessionmaker[Session],
    *,
    handle: str,
    scopes: list[str],
) -> tuple[ActorModel, str]:
    with session_factory() as session:
        actor = create_actor(
            session,
            ActorModel.model_validate(
                {
                    "id": str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"https://agenticqueue.ai/tests/{handle}",
                        )
                    ),
                    "handle": handle,
                    "actor_type": "admin",
                    "display_name": "Schema Admin",
                    "auth_subject": f"{handle}-subject",
                    "is_active": True,
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        session.flush()
        _, raw_token = issue_api_token(
            session,
            actor_id=actor.id,
            scopes=scopes,
            expires_at=None,
        )
        session.commit()
        return actor, raw_token


def _seed_project(session_factory: sessionmaker[Session]) -> uuid.UUID:
    with session_factory() as session:
        workspace = create_workspace(
            session,
            WorkspaceModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "slug": "schema-workspace",
                    "name": "Schema Workspace",
                    "description": "Workspace for coding-task schema tests",
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
                    "slug": "schema-project",
                    "name": "Schema Project",
                    "description": "Project for coding-task schema tests",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        session.commit()
        return project.id


def _task_payload(project_id: uuid.UUID, contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "project_id": str(project_id),
        "task_type": "coding-task",
        "title": "Schema task",
        "state": "queued",
        "description": "Task payload used to validate coding-task contracts",
        "contract": contract,
        "definition_of_done": contract["dod_checklist"],
        "created_at": "2026-04-20T00:00:00+00:00",
        "updated_at": "2026-04-20T00:00:00+00:00",
    }


def _auth_headers(token: str) -> dict[str, str]:
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
def registry() -> TaskTypeRegistry:
    task_type_registry = TaskTypeRegistry(get_task_types_dir())
    task_type_registry.load()
    return task_type_registry


@pytest.fixture
def project_id(session_factory: sessionmaker[Session]) -> uuid.UUID:
    return _seed_project(session_factory)


@pytest.fixture
def admin_token(session_factory: sessionmaker[Session]) -> str:
    _, token = _seed_actor(
        session_factory,
        handle="coding-task-schema-admin",
        scopes=["task:read", "task:write"],
    )
    return token


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app(session_factory=session_factory)
    with TestClient(app) as test_client:
        yield test_client


def test_coding_task_example_instance_validates_without_errors(
    registry: TaskTypeRegistry,
) -> None:
    registry.validate_contract("coding-task", _example_contract())


def test_coding_task_registry_rejects_unknown_task_type(
    registry: TaskTypeRegistry,
) -> None:
    with pytest.raises(SchemaLoadError, match="Unknown task type: review-task"):
        registry.validate_contract("review-task", _example_contract())


def test_coding_task_missing_spec_raises_validation_error_and_api_422(
    registry: TaskTypeRegistry,
    client: TestClient,
    admin_token: str,
    project_id: uuid.UUID,
) -> None:
    invalid = _example_contract()
    invalid.pop("spec")

    with pytest.raises(ValidationError, match="'spec' is a required property"):
        registry.validate_contract("coding-task", invalid)

    response = client.post(
        "/v1/tasks",
        headers=_auth_headers(admin_token),
        json=_task_payload(project_id, invalid),
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "validation_error"


def test_coding_task_rejects_autonomy_tier_outside_allowed_enum(
    registry: TaskTypeRegistry,
) -> None:
    invalid = _example_contract()
    invalid["autonomy_tier"] = 6

    with pytest.raises(ValidationError, match="6 is not one of"):
        registry.validate_contract("coding-task", invalid)


def test_coding_task_rejects_empty_dod_checklist(
    registry: TaskTypeRegistry,
) -> None:
    invalid = _example_contract()
    invalid["dod_checklist"] = []

    with pytest.raises(ValidationError, match=r"\[\] should be non-empty"):
        registry.validate_contract("coding-task", invalid)


def test_coding_task_rejects_learning_items_missing_required_fields(
    registry: TaskTypeRegistry,
) -> None:
    invalid = _example_contract()
    invalid["output"]["learnings"][0].pop("action_rule")

    with pytest.raises(
        ValidationError,
        match="'action_rule' is a required property",
    ):
        registry.validate_contract("coding-task", invalid)


def test_coding_task_rejects_malformed_dod_checks_payload(
    registry: TaskTypeRegistry,
) -> None:
    invalid = _example_contract()
    invalid["dod_checks"] = {"item": "Not an array"}

    with pytest.raises(ValidationError, match="is not of type 'array'"):
        registry.validate_contract("coding-task", invalid)
