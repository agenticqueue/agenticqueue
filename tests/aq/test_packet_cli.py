from __future__ import annotations

import json
from pathlib import Path
import uuid

import pytest
import sqlalchemy as sa
import typer
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from agenticqueue_api.compiler import compile_packet
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import (
    ActorModel,
    CapabilityKey,
    CapabilityRecord,
    ProjectModel,
    TaskModel,
    WorkspaceModel,
)
from agenticqueue_api.packet_versions import packet_content_hash
from agenticqueue_api.repo import (
    create_actor,
    create_project,
    create_task,
    create_workspace,
)
from agenticqueue_cli.commands.packet import register_packet_command

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
def cli_app(session_factory: sessionmaker[Session]) -> typer.Typer:
    app = typer.Typer()

    @app.callback()
    def main() -> None:
        """Test root app for subcommand registration."""

    register_packet_command(app, session_factory=session_factory)
    return app


def _seed_task(session_factory: sessionmaker[Session], *, handle: str) -> uuid.UUID:
    contract = _example_contract()
    with session_factory() as session:
        create_actor(session, _actor_payload(handle=handle))
        workspace = create_workspace(
            session,
            WorkspaceModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "slug": f"{handle}-workspace",
                    "name": f"{handle.title()} Workspace",
                    "description": "Packet CLI tests",
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
                    "description": "Packet CLI tests",
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
                    "title": "Compile packet over CLI",
                    "state": "queued",
                    "description": "Render one packet from the local CLI.",
                    "contract": contract,
                    "definition_of_done": contract["dod_checklist"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        session.commit()
        return task.id


def test_packet_command_renders_markdown_by_default(
    cli_app: typer.Typer,
    session_factory: sessionmaker[Session],
) -> None:
    task_id = _seed_task(session_factory, handle="packet-cli-markdown")
    runner = CliRunner()

    result = runner.invoke(cli_app, ["packet", str(task_id)])

    assert result.exit_code == 0
    assert "# Packet" in result.output
    assert "## Task" in result.output
    assert str(task_id) in result.output
    assert "## Definition Of Done" in result.output
    assert "## Expected Output Schema" in result.output


def test_packet_command_can_emit_json(
    cli_app: typer.Typer,
    session_factory: sessionmaker[Session],
) -> None:
    task_id = _seed_task(session_factory, handle="packet-cli-json")
    runner = CliRunner()

    result = runner.invoke(cli_app, ["packet", str(task_id), "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["task"]["id"] == str(task_id)
    assert payload["task_contract"]["repo"] == "github.com/agenticqueue/agenticqueue"
    assert set(payload) >= {
        "task",
        "task_contract",
        "definition_of_done",
        "repo_scope",
        "permissions",
        "packet_version_id",
    }


def test_packet_command_version_flag_prints_content_hash(
    cli_app: typer.Typer,
    session_factory: sessionmaker[Session],
) -> None:
    task_id = _seed_task(session_factory, handle="packet-cli-version")
    runner = CliRunner()

    with session_factory() as session:
        expected_packet = compile_packet(session, task_id)
        session.commit()
    expected_hash = packet_content_hash(expected_packet)

    result = runner.invoke(cli_app, ["packet", str(task_id), "--version"])

    assert result.exit_code == 0
    assert result.output.strip() == expected_hash


def test_packet_command_missing_task_exits_non_zero_with_clear_error(
    cli_app: typer.Typer,
) -> None:
    runner = CliRunner()
    missing_id = uuid.uuid4()

    result = runner.invoke(cli_app, ["packet", str(missing_id)])

    assert result.exit_code == 1
    assert f"Task not found: {missing_id}" in result.output
