from __future__ import annotations

import json
from collections.abc import Iterator
import uuid

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
    PolicyRecord,
    ProjectRecord,
    TaskRecord,
    WorkspaceRecord,
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
    "learning",
    "task",
    "project",
    "policy",
    "audit_log",
    "workspace",
    "actor",
]

runner = CliRunner()


def _setup_headers() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


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


def _run_init() -> dict[str, str | None]:
    result = runner.invoke(cli_app, ["init"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_setup_endpoint_bootstraps_workspace_and_attaches_default_policy(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    setup = client.post("/setup", headers=_setup_headers())

    assert setup.status_code == 201
    payload = setup.json()
    token = payload["api_token"]
    assert token is not None
    assert payload["status"] == "initialized"
    assert payload["policy_name"] == "default-coding"

    response = client.get(
        "/v1/workspaces",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    workspaces = response.json()
    assert len(workspaces) == 1
    assert workspaces[0]["id"] == payload["workspace_id"]
    assert workspaces[0]["policy_id"] == payload["policy_id"]

    with session_factory() as session:
        policy = session.get(PolicyRecord, payload["policy_id"])
    assert policy is not None
    assert policy.name == "default-coding"


def test_setup_endpoint_is_disabled_after_first_run(client: TestClient) -> None:
    first = client.post("/setup", headers=_setup_headers())
    assert first.status_code == 201

    second = client.post("/setup", headers=_setup_headers())

    assert second.status_code == 409
    assert second.json()["message"] == "First-run setup already completed"


def test_init_command_is_idempotent_and_only_emits_token_once(
    session_factory: sessionmaker[Session],
) -> None:
    first_run = _run_init()
    second_run = _run_init()

    assert first_run["status"] == "initialized"
    assert first_run["api_token"] is not None
    assert first_run["policy_name"] == "default-coding"

    assert second_run["status"] == "noop"
    assert second_run["api_token"] is None
    assert (
        second_run["message"] == "Existing workspace detected; first-run init skipped."
    )

    assert _count_rows(session_factory, WorkspaceRecord) == 1
    assert _count_rows(session_factory, ProjectRecord) == 1
    assert _count_rows(session_factory, ActorRecord) == 1
    assert _count_rows(session_factory, ApiTokenRecord) == 1
    assert _count_rows(session_factory, TaskRecord) == 1
    assert _count_rows(session_factory, PolicyRecord) == 1
