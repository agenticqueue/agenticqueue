from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from agenticqueue_api.app import create_app
from agenticqueue_api.cli import app as cli_app
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import (
    ActorRecord,
    ApiTokenRecord,
    ProjectRecord,
    TaskRecord,
    WorkspaceRecord,
)
from agenticqueue_api.seed import load_seed_fixture

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
    "audit_log",
    "workspace",
    "actor",
]

runner = CliRunner()


def truncate_all_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        qualified_tables = ", ".join(
            f"agenticqueue.{table_name}" for table_name in TRUNCATE_TABLES
        )
        connection.execute(
            sa.text(f"TRUNCATE TABLE {qualified_tables} RESTART IDENTITY CASCADE")
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


def _count_rows(
    session_factory: sessionmaker[Session],
    record_type: type[object],
) -> int:
    with session_factory() as session:
        statement = sa.select(sa.func.count()).select_from(record_type)
        return int(session.scalar(statement) or 0)


def _seed_once() -> dict[str, str]:
    result = runner.invoke(cli_app, ["seed"])
    assert result.exit_code == 0
    return json.loads(result.stdout)


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_seed_happy_path_creates_expected_entities_and_claimable_task(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    fixture = load_seed_fixture()

    seeded = _seed_once()

    assert seeded == {
        "actor_id": str(fixture.actor.id),
        "api_token": fixture.token.render_raw_token(),
        "project_id": str(fixture.project.id),
        "task_id": str(fixture.task.id),
        "workspace_id": str(fixture.workspace.id),
    }
    assert _count_rows(session_factory, WorkspaceRecord) == 1
    assert _count_rows(session_factory, ProjectRecord) == 1
    assert _count_rows(session_factory, ActorRecord) == 1
    assert _count_rows(session_factory, ApiTokenRecord) == 1
    assert _count_rows(session_factory, TaskRecord) == 1

    response = client.get(
        "/v1/tasks",
        headers=_auth_headers(seeded["api_token"]),
        params={"state": "todo"},
    )

    assert response.status_code == 200
    tasks = response.json()
    assert [task["id"] for task in tasks] == [str(fixture.task.id)]
    assert tasks[0]["project_id"] == str(fixture.project.id)
    assert tasks[0]["task_type"] == fixture.task.task_type
    assert tasks[0]["state"] == fixture.task.state
    assert tasks[0]["contract"] == fixture.task.contract
    assert tasks[0]["definition_of_done"] == fixture.task.definition_of_done


def test_seed_is_idempotent_across_two_runs(
    session_factory: sessionmaker[Session],
) -> None:
    first_run = _seed_once()
    second_run = _seed_once()

    assert second_run == first_run
    assert _count_rows(session_factory, WorkspaceRecord) == 1
    assert _count_rows(session_factory, ProjectRecord) == 1
    assert _count_rows(session_factory, ActorRecord) == 1
    assert _count_rows(session_factory, ApiTokenRecord) == 1
    assert _count_rows(session_factory, TaskRecord) == 1


def test_seed_uses_fixture_ids_from_examples_yaml(
    session_factory: sessionmaker[Session],
) -> None:
    fixture = load_seed_fixture()
    _seed_once()

    with session_factory() as session:
        workspace = session.get(WorkspaceRecord, fixture.workspace.id)
        project = session.get(ProjectRecord, fixture.project.id)
        actor = session.get(ActorRecord, fixture.actor.id)
        token = session.get(ApiTokenRecord, fixture.token.id)
        task = session.get(TaskRecord, fixture.task.id)

    assert workspace is not None
    assert project is not None
    assert actor is not None
    assert token is not None
    assert task is not None


def test_seed_fixture_loads_independent_of_current_working_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)

    fixture = load_seed_fixture()

    assert fixture.workspace.slug
    assert fixture.project.slug
    assert fixture.actor.handle
