from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
import uuid

from fastapi.testclient import TestClient
from fastmcp import Client as FastMCPClient
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner
import typer

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.capabilities import grant_capability
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.memory import MemoryItemRecord, MemoryLayer
from agenticqueue_api.mcp import build_memory_mcp
from agenticqueue_api.models import (
    ActorModel,
    CapabilityKey,
    CapabilityRecord,
    LearningModel,
    ProjectModel,
    TaskModel,
    WorkspaceModel,
)
from agenticqueue_api.repo import (
    create_actor,
    create_learning,
    create_project,
    create_task,
    create_workspace,
)
from agenticqueue_cli.commands.memory import build_memory_app

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
    "memory_item",
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


def _example_contract(
    surface_area: list[str],
    file_scope: list[str],
    spec: str,
) -> dict[str, Any]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["surface_area"] = surface_area
    contract["file_scope"] = file_scope
    contract["spec"] = spec
    return contract


def _uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{label}")


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
            "id": str(_uuid(handle)),
            "handle": handle,
            "actor_type": actor_type,
            "display_name": handle.replace("-", " ").title(),
            "auth_subject": f"{handle}-subject",
            "is_active": True,
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )


def _mcp_call(server, tool_name: str, arguments: dict[str, object]) -> dict[str, Any]:
    async def _invoke() -> dict[str, Any]:
        async with FastMCPClient(server) as client:
            result = await client.call_tool(tool_name, arguments)
            return result.data

    return asyncio.run(_invoke())


def _headers(token: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = str(_uuid(idempotency_key))
    return headers


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


@pytest.fixture
def cli_app(session_factory: sessionmaker[Session]) -> typer.Typer:
    app = typer.Typer()

    @app.callback()
    def main() -> None:
        """Test root app for subcommand registration."""

    app.add_typer(build_memory_app(session_factory=session_factory), name="memory")
    return app


def _seed_search_state(
    session_factory: sessionmaker[Session],
) -> tuple[uuid.UUID, str]:
    with session_factory() as session:
        actor = create_actor(
            session,
            _actor_payload(handle="memory-search-agent"),
        )
        workspace = create_workspace(
            session,
            WorkspaceModel.model_validate(
                {
                    "id": str(_uuid("memory-search-workspace")),
                    "slug": "memory-search-workspace",
                    "name": "Memory Search Workspace",
                    "description": "Workspace for AQ-86 search tests",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        project = create_project(
            session,
            ProjectModel.model_validate(
                {
                    "id": str(_uuid("memory-search-project")),
                    "workspace_id": str(workspace.id),
                    "slug": "memory-search-project",
                    "name": "Memory Search Project",
                    "description": "Project for AQ-86 search tests",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        relevant_task = create_task(
            session,
            TaskModel.model_validate(
                {
                    "id": str(_uuid("memory-search-task")),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": "Transport parity retrieval",
                    "state": "done",
                    "description": "Expose memory search over every surface.",
                    "contract": _example_contract(
                        ["memory", "transport", "retrieval"],
                        ["apps/api/src/agenticqueue_api/routers/memory.py"],
                        "Keep REST, CLI, and MCP retrieval outputs aligned.",
                    ),
                    "definition_of_done": ["parity", "retrieval"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        unrelated_task = create_task(
            session,
            TaskModel.model_validate(
                {
                    "id": str(_uuid("memory-search-task-unrelated")),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": "Separate auth hardening work",
                    "state": "done",
                    "description": "Unrelated learning seed.",
                    "contract": _example_contract(
                        ["auth", "rbac"],
                        ["apps/api/src/agenticqueue_api/roles.py"],
                        "Keep RBAC coverage high.",
                    ),
                    "definition_of_done": ["rbac"],
                    "created_at": "2026-04-20T00:05:00+00:00",
                    "updated_at": "2026-04-20T00:05:00+00:00",
                }
            ),
        )
        create_learning(
            session,
            LearningModel.model_validate(
                {
                    "id": str(_uuid("memory-search-learning")),
                    "task_id": str(relevant_task.id),
                    "owner_actor_id": str(actor.id),
                    "owner": "memory-search-agent",
                    "title": "Keep transport parity across memory search",
                    "learning_type": "pattern",
                    "what_happened": "Transport parity drifted in an earlier memory search draft.",
                    "what_learned": "Search output should come from one shared retrieval layer.",
                    "action_rule": "Route REST, CLI, and MCP memory search through one service.",
                    "applies_when": "A read surface ships across multiple transports.",
                    "does_not_apply_when": "Only one transport exists.",
                    "evidence": ["artifact://memory-search-parity"],
                    "scope": "project",
                    "confidence": "confirmed",
                    "status": "active",
                    "review_date": "2026-05-20",
                    "embedding": None,
                    "created_at": "2026-04-20T00:10:00+00:00",
                    "updated_at": "2026-04-20T00:10:00+00:00",
                }
            ),
        )
        create_learning(
            session,
            LearningModel.model_validate(
                {
                    "id": str(_uuid("memory-search-learning-unrelated")),
                    "task_id": str(unrelated_task.id),
                    "owner_actor_id": str(actor.id),
                    "owner": "memory-search-agent",
                    "title": "Keep RBAC mutation logs complete",
                    "learning_type": "tooling",
                    "what_happened": "An RBAC refactor missed an audit row.",
                    "what_learned": "Mutation paths should always log before commit.",
                    "action_rule": "Write the audit row inside the shared mutation helper.",
                    "applies_when": "Touching RBAC write paths.",
                    "does_not_apply_when": "The endpoint is read-only.",
                    "evidence": ["artifact://rbac-audit"],
                    "scope": "project",
                    "confidence": "confirmed",
                    "status": "active",
                    "review_date": "2026-05-21",
                    "embedding": None,
                    "created_at": "2026-04-20T00:12:00+00:00",
                    "updated_at": "2026-04-20T00:12:00+00:00",
                }
            ),
        )
        grant_capability(
            session,
            actor_id=actor.id,
            capability=CapabilityKey.SEARCH_MEMORY,
            scope={"project_id": str(project.id)},
            granted_by_actor_id=actor.id,
        )
        _, token = issue_api_token(
            session,
            actor_id=actor.id,
            scopes=["memory:read"],
            expires_at=None,
        )
        session.commit()
        return project.id, token


def _seed_admin_token(
    session_factory: sessionmaker[Session],
    *,
    handle: str,
    scopes: list[str],
) -> tuple[uuid.UUID, str]:
    with session_factory() as session:
        actor = create_actor(
            session,
            _actor_payload(handle=handle, actor_type="admin"),
        )
        _, token = issue_api_token(
            session,
            actor_id=actor.id,
            scopes=scopes,
            expires_at=None,
        )
        session.commit()
        return actor.id, token


def _seed_memory_rows(
    session_factory: sessionmaker[Session],
) -> tuple[uuid.UUID, str]:
    project_id = _uuid("memory-stats-project")
    _, token = _seed_admin_token(
        session_factory,
        handle="memory-stats-admin",
        scopes=["memory:read"],
    )
    with session_factory() as session:
        session.add_all(
            [
                MemoryItemRecord(
                    id=_uuid("memory-stats-project-row"),
                    layer=MemoryLayer.PROJECT,
                    scope_id=project_id,
                    content_text="Project-scoped retrieval note",
                    content_hash="stats-project-hash",
                    embedding=None,
                    source_ref="docs/project.md",
                    surface_area=["memory", "project"],
                ),
                MemoryItemRecord(
                    id=_uuid("memory-stats-user-row"),
                    layer=MemoryLayer.USER,
                    scope_id=_uuid("memory-stats-user"),
                    content_text="User-scoped retrieval note",
                    content_hash="stats-user-hash",
                    embedding=None,
                    source_ref="docs/user.md",
                    surface_area=["memory", "user"],
                ),
            ]
        )
        session.commit()
    return project_id, token


def test_search_memory_rest_returns_tiered_results(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    project_id, token = _seed_search_state(session_factory)

    response = client.post(
        "/v1/memory/search",
        headers=_headers(token, idempotency_key="memory-search-rest"),
        json={
            "query": "transport parity retrieval",
            "scope": {"project_id": str(project_id)},
            "k": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["title"] for item in payload["items"]] == [
        "Keep transport parity across memory search"
    ]
    assert "surface_area" in payload["tiers_fired"]
    assert "metadata" in payload["tiers_fired"]
    assert "fts" in payload["tiers_fired"]


def test_search_memory_cli_matches_rest(
    client: TestClient,
    cli_app: typer.Typer,
    session_factory: sessionmaker[Session],
) -> None:
    project_id, token = _seed_search_state(session_factory)
    rest_response = client.post(
        "/v1/memory/search",
        headers=_headers(token, idempotency_key="memory-search-cli-rest"),
        json={
            "query": "transport parity retrieval",
            "scope": {"project_id": str(project_id)},
            "k": 5,
        },
    )
    runner = CliRunner()

    cli_result = runner.invoke(
        cli_app,
        [
            "memory",
            "search",
            "transport parity retrieval",
            "--project-id",
            str(project_id),
            "--token",
            token,
        ],
    )

    assert cli_result.exit_code == 0
    assert json.loads(cli_result.output) == rest_response.json()


def test_search_memory_mcp_matches_rest(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    project_id, token = _seed_search_state(session_factory)
    rest_response = client.post(
        "/v1/memory/search",
        headers=_headers(token, idempotency_key="memory-search-mcp-rest"),
        json={
            "query": "transport parity retrieval",
            "scope": {"project_id": str(project_id)},
            "k": 5,
        },
    )
    mcp = build_memory_mcp(session_factory=session_factory)

    mcp_response = _mcp_call(
        mcp,
        "search_memory",
        {
            "query": "transport parity retrieval",
            "scope": {"project_id": str(project_id)},
            "k": 5,
            "token": token,
        },
    )

    assert mcp_response == rest_response.json()


def test_sync_memory_rest_ingests_files(
    client: TestClient,
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    project_id = _uuid("memory-sync-project")
    _, token = _seed_admin_token(
        session_factory,
        handle="memory-sync-admin-rest",
        scopes=["memory:write"],
    )
    root = tmp_path / "memory-sync-rest"
    root.mkdir()
    (root / "alpha.md").write_text("alpha retrieval note", encoding="utf-8")
    (root / "beta.md").write_text("beta retrieval note", encoding="utf-8")

    response = client.post(
        "/v1/memory/sync",
        headers=_headers(token, idempotency_key="memory-sync-rest"),
        json={
            "layer": "project",
            "scope_id": str(project_id),
            "paths": [str(root)],
            "full_sync": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["layer"] == "project"
    assert payload["scope_id"] == str(project_id)
    assert payload["upserted"] == 2
    assert payload["pruned"] == 0
    assert payload["full_sync"] is True
    assert payload["partial"] is False


def test_sync_memory_cli_matches_rest(
    client: TestClient,
    cli_app: typer.Typer,
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    project_id = _uuid("memory-sync-cli-project")
    _, token = _seed_admin_token(
        session_factory,
        handle="memory-sync-admin-cli",
        scopes=["memory:write"],
    )
    root = tmp_path / "memory-sync-cli"
    root.mkdir()
    (root / "notes.md").write_text("cli sync parity", encoding="utf-8")
    rest_response = client.post(
        "/v1/memory/sync",
        headers=_headers(token, idempotency_key="memory-sync-cli-rest"),
        json={
            "layer": "project",
            "scope_id": str(project_id),
            "paths": [str(root)],
            "full_sync": True,
        },
    )
    runner = CliRunner()

    cli_result = runner.invoke(
        cli_app,
        [
            "memory",
            "sync",
            str(project_id),
            "--layer",
            "project",
            "--path",
            str(root),
            "--full-sync",
            "--token",
            token,
        ],
    )

    assert cli_result.exit_code == 0
    assert json.loads(cli_result.output) == rest_response.json()


def test_sync_memory_mcp_matches_rest(
    client: TestClient,
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    project_id = _uuid("memory-sync-mcp-project")
    _, token = _seed_admin_token(
        session_factory,
        handle="memory-sync-admin-mcp",
        scopes=["memory:write"],
    )
    root = tmp_path / "memory-sync-mcp"
    root.mkdir()
    (root / "notes.md").write_text("mcp sync parity", encoding="utf-8")
    rest_response = client.post(
        "/v1/memory/sync",
        headers=_headers(token, idempotency_key="memory-sync-mcp-rest"),
        json={
            "layer": "project",
            "scope_id": str(project_id),
            "paths": [str(root)],
            "full_sync": True,
        },
    )
    mcp = build_memory_mcp(session_factory=session_factory)

    mcp_response = _mcp_call(
        mcp,
        "sync_memory",
        {
            "layer": "project",
            "scope_id": str(project_id),
            "paths": [str(root)],
            "full_sync": True,
            "token": token,
        },
    )

    assert mcp_response == rest_response.json()


def test_memory_stats_rest_returns_layer_counts(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    project_id, token = _seed_memory_rows(session_factory)

    response = client.get(
        "/v1/memory/stats",
        headers=_headers(token),
        params={"layer": "project", "scope_id": str(project_id)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["layer"] == "project"
    assert payload["scope_id"] == str(project_id)
    assert payload["total_items"] == 1
    assert payload["by_layer"]["project"] == 1
    assert payload["by_layer"]["user"] == 0


def test_memory_stats_cli_matches_rest(
    client: TestClient,
    cli_app: typer.Typer,
    session_factory: sessionmaker[Session],
) -> None:
    project_id, token = _seed_memory_rows(session_factory)
    rest_response = client.get(
        "/v1/memory/stats",
        headers=_headers(token),
        params={"layer": "project", "scope_id": str(project_id)},
    )
    runner = CliRunner()

    cli_result = runner.invoke(
        cli_app,
        [
            "memory",
            "stats",
            "--layer",
            "project",
            "--scope-id",
            str(project_id),
            "--token",
            token,
        ],
    )

    assert cli_result.exit_code == 0
    assert json.loads(cli_result.output) == rest_response.json()


def test_memory_stats_mcp_matches_rest(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    project_id, token = _seed_memory_rows(session_factory)
    rest_response = client.get(
        "/v1/memory/stats",
        headers=_headers(token),
        params={"layer": "project", "scope_id": str(project_id)},
    )
    mcp = build_memory_mcp(session_factory=session_factory)

    mcp_response = _mcp_call(
        mcp,
        "memory_stats",
        {
            "layer": "project",
            "scope_id": str(project_id),
            "token": token,
        },
    )

    assert mcp_response == rest_response.json()
