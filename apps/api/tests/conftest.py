from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.capabilities import grant_capability
from agenticqueue_api.config import (
    get_psycopg_connect_args,
    get_sqlalchemy_sync_database_url,
)
from agenticqueue_api.models import (
    ActorModel,
    CapabilityKey,
    CapabilityRecord,
    ProjectModel,
    TaskModel,
    WorkspaceModel,
)
from agenticqueue_api.repo import (
    create_actor,
    create_project,
    create_task,
    create_workspace,
)

os.environ.setdefault("AQ_ADMIN_PASSCODE", "test-admin-passcode")


def actor_id_for(handle: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{handle}")


def model_from(model_type: type[Any], payload: dict[str, Any]) -> Any:
    return model_type.model_validate(payload)


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        connect_args=get_psycopg_connect_args(),
        future=True,
    )


def _schema_tables(connection: sa.Connection) -> list[str]:
    return list(connection.scalars(sa.text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'agenticqueue'
                  AND table_type = 'BASE TABLE'
                  AND table_name <> 'alembic_version'
                ORDER BY table_name
                """)))


def truncate_all_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        table_names = _schema_tables(connection)
        if table_names:
            qualified = ", ".join(f"agenticqueue.{name}" for name in table_names)
            connection.execute(
                sa.text(f"TRUNCATE TABLE {qualified} RESTART IDENTITY CASCADE")
            )
        if "capability" in table_names:
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
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


def make_actor_payload(
    *,
    handle: str,
    actor_type: str,
    display_name: str,
) -> ActorModel:
    return ActorModel.model_validate(
        {
            "id": str(actor_id_for(handle)),
            "handle": handle,
            "actor_type": actor_type,
            "display_name": display_name,
            "auth_subject": f"{handle}-subject",
            "is_active": True,
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )


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


def seed_token(
    session_factory: sessionmaker[Session],
    *,
    actor_id: uuid.UUID,
    scopes: list[str],
) -> tuple[str, uuid.UUID]:
    with session_factory() as session:
        token, raw_token = issue_api_token(
            session,
            actor_id=actor_id,
            scopes=scopes,
            expires_at=None,
        )
        session.commit()
        return raw_token, token.id


def seed_capability(
    session_factory: sessionmaker[Session],
    *,
    actor_id: uuid.UUID,
    capability: CapabilityKey,
    project_id: uuid.UUID | None = None,
) -> None:
    with session_factory() as session:
        grant_capability(
            session,
            actor_id=actor_id,
            capability=capability,
            scope={} if project_id is None else {"project_id": str(project_id)},
        )
        session.commit()


def seed_workspace(
    session_factory: sessionmaker[Session],
    *,
    slug: str,
    name: str,
) -> uuid.UUID:
    with session_factory() as session:
        workspace = create_workspace(
            session,
            model_from(
                WorkspaceModel,
                {
                    "id": str(uuid.uuid4()),
                    "slug": slug,
                    "name": name,
                    "description": "Seed workspace",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                },
            ),
        )
        session.commit()
        return workspace.id


def seed_project(
    session_factory: sessionmaker[Session],
    *,
    workspace_id: uuid.UUID,
    slug: str,
    name: str,
) -> uuid.UUID:
    with session_factory() as session:
        project = create_project(
            session,
            model_from(
                ProjectModel,
                {
                    "id": str(uuid.uuid4()),
                    "workspace_id": str(workspace_id),
                    "slug": slug,
                    "name": name,
                    "description": "Seed project",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                },
            ),
        )
        session.commit()
        return project.id


def coding_task_payload(*, project_id: uuid.UUID, title: str) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "project_id": str(project_id),
        "task_type": "coding-task",
        "title": title,
        "state": "queued",
        "description": "Seed task",
        "contract": {
            "repo": "github.com/agenticqueue/agenticqueue",
            "branch": "main",
            "file_scope": ["apps/api/src/agenticqueue_api/app.py"],
            "surface_area": ["apps/api"],
            "spec": "## Goal\nShip the requested coding-task change.\n",
            "dod_checklist": ["done"],
            "autonomy_tier": 3,
            "output": {
                "diff_url": "artifacts/diffs/test.patch",
                "test_report": "artifacts/tests/test.txt",
                "artifacts": [
                    {
                        "kind": "diff",
                        "uri": "artifacts/diffs/test.patch",
                    }
                ],
                "learnings": [],
            },
        },
        "definition_of_done": ["done"],
        "created_at": "2026-04-20T00:00:00+00:00",
        "updated_at": "2026-04-20T00:00:00+00:00",
    }


def seed_task(
    session_factory: sessionmaker[Session],
    *,
    project_id: uuid.UUID,
    title: str,
) -> uuid.UUID:
    with session_factory() as session:
        task = create_task(
            session,
            model_from(
                TaskModel,
                coding_task_payload(project_id=project_id, title=title),
            ),
        )
        session.commit()
        return task.id
