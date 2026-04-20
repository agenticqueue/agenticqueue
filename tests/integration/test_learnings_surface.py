from __future__ import annotations

import asyncio
import copy
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

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.capabilities import grant_capability
from agenticqueue_api.cli import app as cli_app
from agenticqueue_api.mcp import build_learnings_mcp
from agenticqueue_api.models import CapabilityKey, CapabilityRecord
from agenticqueue_api.models.audit_log import AuditLogRecord
from agenticqueue_api.models.learning import LearningModel
from agenticqueue_api.models.project import ProjectModel
from agenticqueue_api.models.task import TaskModel
from agenticqueue_api.models.workspace import WorkspaceModel
from agenticqueue_api.models.actor import ActorModel
from agenticqueue_api.repo import (
    create_actor,
    create_learning,
    create_project,
    create_task,
    create_workspace,
)
from agenticqueue_api.config import get_sqlalchemy_sync_database_url

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


def _example_contract(
    surface_area: list[str], file_scope: list[str], spec: str
) -> dict[str, Any]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["surface_area"] = surface_area
    contract["file_scope"] = file_scope
    contract["spec"] = spec
    return contract


def _deterministic_uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{label}")


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


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> None:
    truncate_all_tables(engine)


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> TestClient:
    with TestClient(create_app(session_factory=session_factory)) as test_client:
        yield test_client


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def _seed_surface_state(
    session_factory: sessionmaker[Session],
) -> dict[str, Any]:
    with session_factory() as session:
        actor = create_actor(
            session,
            ActorModel.model_validate(
                {
                    "id": str(_deterministic_uuid("surface-actor")),
                    "handle": "surface-agent",
                    "actor_type": "agent",
                    "display_name": "Surface Agent",
                    "auth_subject": "surface-agent-subject",
                    "is_active": True,
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        workspace = create_workspace(
            session,
            WorkspaceModel.model_validate(
                {
                    "id": str(_deterministic_uuid("surface-workspace")),
                    "slug": "surface-workspace",
                    "name": "Surface Workspace",
                    "description": "Workspace for learnings surface tests",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        project = create_project(
            session,
            ProjectModel.model_validate(
                {
                    "id": str(_deterministic_uuid("surface-project")),
                    "workspace_id": str(workspace.id),
                    "slug": "surface-project",
                    "name": "Surface Project",
                    "description": "Project for learnings surface tests",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        task = create_task(
            session,
            TaskModel.model_validate(
                {
                    "id": str(_deterministic_uuid("surface-task")),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": "Keep learning surfaces aligned",
                    "state": "done",
                    "description": "Transport parity task",
                    "contract": _example_contract(
                        ["learnings", "rest", "cli"],
                        ["apps/api/src/agenticqueue_api/cli.py"],
                        "Align REST, CLI, and MCP learning payloads.",
                    ),
                    "definition_of_done": ["parity", "tests"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        search_task = create_task(
            session,
            TaskModel.model_validate(
                {
                    "id": str(_deterministic_uuid("surface-search-task")),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": "Expose MCP learnings tools",
                    "state": "done",
                    "description": "Searchable MCP transport task",
                    "contract": _example_contract(
                        ["learnings", "mcp"],
                        ["apps/api/src/agenticqueue_api/mcp/learnings_tools.py"],
                        "Add MCP learnings tools with parity tests.",
                    ),
                    "definition_of_done": ["mcp", "tests"],
                    "created_at": "2026-04-20T00:05:00+00:00",
                    "updated_at": "2026-04-20T00:05:00+00:00",
                }
            ),
        )
        relevant_learning = create_learning(
            session,
            LearningModel.model_validate(
                {
                    "id": str(_deterministic_uuid("relevant-learning")),
                    "task_id": str(task.id),
                    "owner_actor_id": str(actor.id),
                    "owner": "surface-agent",
                    "title": "Keep learning payloads identical across transports",
                    "learning_type": "pattern",
                    "what_happened": "REST and CLI payloads drifted during a prior refactor.",
                    "what_learned": "Route every transport through one shared operation layer.",
                    "action_rule": "Share models and auth checks across transports.",
                    "applies_when": "The same feature ships on REST, CLI, and MCP.",
                    "does_not_apply_when": "Only one transport exists.",
                    "evidence": ["artifact://transport-parity"],
                    "scope": "task",
                    "confidence": "confirmed",
                    "status": "active",
                    "review_date": "2026-05-20",
                    "embedding": None,
                    "created_at": "2026-04-20T00:10:00+00:00",
                    "updated_at": "2026-04-20T00:10:00+00:00",
                }
            ),
        )
        promotable_learning = create_learning(
            session,
            LearningModel.model_validate(
                {
                    "id": str(_deterministic_uuid("promotable-learning")),
                    "task_id": str(task.id),
                    "owner_actor_id": str(actor.id),
                    "owner": "surface-agent",
                    "title": "Promote parity learnings when they repeat",
                    "learning_type": "pattern",
                    "what_happened": "Transport parity issues reappeared twice.",
                    "what_learned": "Promotion should be explicit and audited.",
                    "action_rule": "Promote confirmed parity learnings to project scope.",
                    "applies_when": "A parity fix repeats in the same project.",
                    "does_not_apply_when": "The issue happened once.",
                    "evidence": ["artifact://promote-parity"],
                    "scope": "task",
                    "confidence": "confirmed",
                    "status": "active",
                    "review_date": "2026-05-21",
                    "embedding": None,
                    "created_at": "2026-04-20T00:11:00+00:00",
                    "updated_at": "2026-04-20T00:11:00+00:00",
                }
            ),
        )
        old_learning = create_learning(
            session,
            LearningModel.model_validate(
                {
                    "id": str(_deterministic_uuid("old-learning")),
                    "task_id": str(task.id),
                    "owner_actor_id": str(actor.id),
                    "owner": "surface-agent",
                    "title": "Old parity workaround",
                    "learning_type": "pitfall",
                    "what_happened": "An older workaround required manual MCP patching.",
                    "what_learned": "The workaround is obsolete.",
                    "action_rule": "Retire the old workaround when the shared transport layer lands.",
                    "applies_when": "The legacy CLI and MCP code paths still diverge.",
                    "does_not_apply_when": "The shared transport layer is in place.",
                    "evidence": ["artifact://old-workaround"],
                    "scope": "task",
                    "confidence": "tentative",
                    "status": "active",
                    "review_date": "2026-05-22",
                    "embedding": None,
                    "created_at": "2026-04-20T00:12:00+00:00",
                    "updated_at": "2026-04-20T00:12:00+00:00",
                }
            ),
        )
        replacement_learning = create_learning(
            session,
            LearningModel.model_validate(
                {
                    "id": str(_deterministic_uuid("replacement-learning")),
                    "task_id": str(search_task.id),
                    "owner_actor_id": str(actor.id),
                    "owner": "surface-agent",
                    "title": "Shared learnings surface replaces transport-specific fixes",
                    "learning_type": "pattern",
                    "what_happened": "The dedicated learnings surface removed custom one-off patches.",
                    "what_learned": "The replacement learning supersedes the old workaround.",
                    "action_rule": "Prefer shared transport operations over bespoke integrations.",
                    "applies_when": "REST, CLI, and MCP touch the same domain logic.",
                    "does_not_apply_when": "Only one transport exists.",
                    "evidence": ["artifact://replacement-pattern"],
                    "scope": "task",
                    "confidence": "validated",
                    "status": "active",
                    "review_date": "2026-05-23",
                    "embedding": None,
                    "created_at": "2026-04-20T00:13:00+00:00",
                    "updated_at": "2026-04-20T00:13:00+00:00",
                }
            ),
        )
        search_learning = create_learning(
            session,
            LearningModel.model_validate(
                {
                    "id": str(_deterministic_uuid("search-learning")),
                    "task_id": str(search_task.id),
                    "owner_actor_id": str(actor.id),
                    "owner": "surface-agent",
                    "title": "Search repo-scoped learnings before adding MCP code",
                    "learning_type": "tooling",
                    "what_happened": "The MCP tool path changed and needed a repo-scope search.",
                    "what_learned": "Filtering by repo scope isolates the right learning quickly.",
                    "action_rule": "Search learnings with repo_scope when the task names a file path.",
                    "applies_when": "A transport ticket names scoped output files.",
                    "does_not_apply_when": "The task is project-wide without path constraints.",
                    "evidence": ["artifact://repo-scope-search"],
                    "scope": "project",
                    "confidence": "confirmed",
                    "status": "active",
                    "review_date": "2026-05-24",
                    "embedding": None,
                    "created_at": "2026-04-20T00:14:00+00:00",
                    "updated_at": "2026-04-20T00:14:00+00:00",
                }
            ),
        )

        for capability in (
            CapabilityKey.READ_LEARNINGS,
            CapabilityKey.SEARCH_MEMORY,
            CapabilityKey.WRITE_LEARNING,
            CapabilityKey.PROMOTE_LEARNING,
        ):
            grant_capability(
                session,
                actor_id=actor.id,
                capability=capability,
                scope={"project_id": str(project.id)},
                granted_by_actor_id=actor.id,
            )

        _, token = issue_api_token(
            session,
            actor_id=actor.id,
            scopes=["learning:read", "learning:write"],
            expires_at=None,
        )
        session.commit()

    return {
        "actor_id": actor.id,
        "project_id": project.id,
        "task_id": task.id,
        "search_task_id": search_task.id,
        "relevant_learning_id": relevant_learning.id,
        "promotable_learning_id": promotable_learning.id,
        "old_learning_id": old_learning.id,
        "replacement_learning_id": replacement_learning.id,
        "search_learning_id": search_learning.id,
        "token": token,
        "submit_learning_object": {
            "title": "Submit learnings through one shared transport layer",
            "type": "pattern",
            "what_happened": "A transport parity ticket needed one manual learning entry.",
            "what_learned": "Manual submissions should reuse the same auth and capability checks.",
            "action_rule": "Submit task learnings through the shared surface helpers.",
            "applies_when": "A task needs a confirmed learning outside the draft flow.",
            "does_not_apply_when": "The draft flow already covers the case.",
            "evidence": ["artifact://manual-submit"],
            "scope": "task",
            "confidence": "confirmed",
            "status": "active",
            "owner": "surface-agent",
            "review_date": "2026-05-25",
        },
    }


def _rest_headers(token: str | None, *, mutation: bool = False) -> dict[str, str]:
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if mutation:
        headers["Idempotency-Key"] = str(uuid.uuid4())
    return headers


def _audit_rows(session_factory: sessionmaker[Session]) -> list[tuple[str, str]]:
    with session_factory() as session:
        rows = session.scalars(
            sa.select(AuditLogRecord).order_by(
                AuditLogRecord.created_at.asc(),
                AuditLogRecord.id.asc(),
            )
        ).all()
    return [(row.entity_type, row.action) for row in rows]


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(payload)
    if "learning" in normalized:
        for field in ("id", "created_at", "updated_at"):
            normalized["learning"].pop(field, None)
    if "items" in normalized:
        for item in normalized["items"]:
            for field in ("id", "created_at", "updated_at"):
                item.pop(field, None)
    return normalized


def _cli_json(result, *, err: bool = False) -> dict[str, Any]:
    raw = result.stderr if err else result.stdout
    return json.loads(raw.strip())


def _mcp_call(server, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async def _invoke() -> dict[str, Any]:
        async with FastMCPClient(server) as client:
            result = await client.call_tool(tool_name, arguments)
            return result.data

    return asyncio.run(_invoke())


def test_read_surfaces_match_for_relevant_and_search(
    client: TestClient,
    cli_runner: CliRunner,
    session_factory: sessionmaker[Session],
) -> None:
    state = _seed_surface_state(session_factory)
    mcp = build_learnings_mcp(session_factory=session_factory)
    audit_before = _audit_rows(session_factory)

    rest_relevant = client.get(
        "/v1/learnings/relevant",
        params={
            "task_id": str(state["task_id"]),
            "actor_id": str(state["actor_id"]),
            "limit": 3,
        },
        headers=_rest_headers(state["token"]),
    )
    assert rest_relevant.status_code == 200

    cli_relevant = cli_runner.invoke(
        cli_app,
        [
            "learnings",
            "get",
            str(state["task_id"]),
            str(state["actor_id"]),
            "--token",
            state["token"],
            "--limit",
            "3",
        ],
    )
    assert cli_relevant.exit_code == 0

    mcp_relevant = _mcp_call(
        mcp,
        "get_relevant_learnings",
        {
            "task_id": str(state["task_id"]),
            "actor_id": str(state["actor_id"]),
            "token": state["token"],
            "limit": 3,
        },
    )

    assert _normalize_payload(rest_relevant.json()) == _normalize_payload(
        _cli_json(cli_relevant)
    )
    assert _normalize_payload(rest_relevant.json()) == _normalize_payload(mcp_relevant)

    rest_search = client.get(
        "/v1/learnings/search",
        params={
            "query": "repo scope",
            "project": str(state["project_id"]),
            "repo_scope": "apps/api/src/agenticqueue_api/mcp/learnings_tools.py",
            "limit": 5,
        },
        headers=_rest_headers(state["token"]),
    )
    assert rest_search.status_code == 200

    cli_search = cli_runner.invoke(
        cli_app,
        [
            "learnings",
            "search",
            "repo scope",
            "--token",
            state["token"],
            "--project",
            str(state["project_id"]),
            "--repo-scope",
            "apps/api/src/agenticqueue_api/mcp/learnings_tools.py",
            "--limit",
            "5",
        ],
    )
    assert cli_search.exit_code == 0

    mcp_search = _mcp_call(
        mcp,
        "search_learnings",
        {
            "query": "repo scope",
            "token": state["token"],
            "project": str(state["project_id"]),
            "repo_scope": "apps/api/src/agenticqueue_api/mcp/learnings_tools.py",
            "limit": 5,
        },
    )

    assert _normalize_payload(rest_search.json()) == _normalize_payload(
        _cli_json(cli_search)
    )
    assert _normalize_payload(rest_search.json()) == _normalize_payload(mcp_search)
    assert _audit_rows(session_factory)[len(audit_before) :] == []


def test_read_surfaces_missing_auth_match(
    client: TestClient,
    cli_runner: CliRunner,
    session_factory: sessionmaker[Session],
) -> None:
    state = _seed_surface_state(session_factory)
    mcp = build_learnings_mcp(session_factory=session_factory)

    rest_response = client.get(
        "/v1/learnings/relevant",
        params={
            "task_id": str(state["task_id"]),
            "actor_id": str(state["actor_id"]),
        },
    )
    assert rest_response.status_code == 401

    cli_response = cli_runner.invoke(
        cli_app,
        [
            "learnings",
            "get",
            str(state["task_id"]),
            str(state["actor_id"]),
        ],
    )
    assert cli_response.exit_code == 1

    mcp_response = _mcp_call(
        mcp,
        "get_relevant_learnings",
        {
            "task_id": str(state["task_id"]),
            "actor_id": str(state["actor_id"]),
        },
    )

    assert rest_response.json() == _cli_json(cli_response, err=True)
    assert rest_response.json() == mcp_response


@pytest.mark.parametrize(
    ("rest_call", "cli_args", "mcp_call", "expected_audit"),
    [
        (
            lambda client, state: client.post(
                "/v1/learnings/submit",
                headers=_rest_headers(state["token"], mutation=True),
                json={
                    "task_id": str(state["task_id"]),
                    "learning_object": state["submit_learning_object"],
                },
            ),
            lambda state: [
                "learnings",
                "submit",
                str(state["task_id"]),
                "--learning-object",
                json.dumps(state["submit_learning_object"]),
                "--token",
                state["token"],
            ],
            lambda mcp, state: _mcp_call(
                mcp,
                "submit_task_learning",
                {
                    "task_id": str(state["task_id"]),
                    "learning_object": state["submit_learning_object"],
                    "token": state["token"],
                },
            ),
            [("learning", "CREATE")],
        ),
        (
            lambda client, state: client.post(
                f"/v1/learnings/{state['promotable_learning_id']}/promote",
                headers=_rest_headers(state["token"], mutation=True),
                json={"target_scope": "project"},
            ),
            lambda state: [
                "learnings",
                "promote",
                str(state["promotable_learning_id"]),
                "project",
                "--token",
                state["token"],
            ],
            lambda mcp, state: _mcp_call(
                mcp,
                "promote_learning",
                {
                    "learning_id": str(state["promotable_learning_id"]),
                    "target_scope": "project",
                    "token": state["token"],
                },
            ),
            [("learning", "UPDATE")],
        ),
        (
            lambda client, state: client.post(
                f"/v1/learnings/{state['old_learning_id']}/supersede",
                headers=_rest_headers(state["token"], mutation=True),
                json={"replaced_by": str(state["replacement_learning_id"])},
            ),
            lambda state: [
                "learnings",
                "supersede",
                str(state["old_learning_id"]),
                str(state["replacement_learning_id"]),
                "--token",
                state["token"],
            ],
            lambda mcp, state: _mcp_call(
                mcp,
                "supersede_learning",
                {
                    "learning_id": str(state["old_learning_id"]),
                    "replaced_by": str(state["replacement_learning_id"]),
                    "token": state["token"],
                },
            ),
            [("learning", "UPDATE"), ("edge", "CREATE")],
        ),
    ],
)
def test_mutation_surfaces_match_payloads_and_audit(
    client: TestClient,
    cli_runner: CliRunner,
    session_factory: sessionmaker[Session],
    engine: Engine,
    rest_call,
    cli_args,
    mcp_call,
    expected_audit,
) -> None:
    results: list[dict[str, Any]] = []
    audits: list[list[tuple[str, str]]] = []

    for transport in ("rest", "cli", "mcp"):
        truncate_all_tables(engine)
        state = _seed_surface_state(session_factory)
        mcp = build_learnings_mcp(session_factory=session_factory)
        audit_before = _audit_rows(session_factory)

        if transport == "rest":
            response = rest_call(client, state)
            assert response.status_code in {200, 201}
            payload = response.json()
        elif transport == "cli":
            result = cli_runner.invoke(cli_app, cli_args(state))
            assert result.exit_code == 0
            payload = _cli_json(result)
        else:
            payload = mcp_call(mcp, state)

        results.append(_normalize_payload(payload))
        audits.append(_audit_rows(session_factory)[len(audit_before) :])

    assert results[0] == results[1] == results[2]
    assert audits[0] == audits[1] == audits[2] == expected_audit


@pytest.mark.parametrize(
    ("rest_path", "rest_json", "cli_args", "mcp_tool", "mcp_args"),
    [
        (
            "/v1/learnings/submit",
            lambda state: {
                "task_id": str(state["task_id"]),
                "learning_object": state["submit_learning_object"],
            },
            lambda state: [
                "learnings",
                "submit",
                str(state["task_id"]),
                "--learning-object",
                json.dumps(state["submit_learning_object"]),
            ],
            "submit_task_learning",
            lambda state: {
                "task_id": str(state["task_id"]),
                "learning_object": state["submit_learning_object"],
            },
        ),
        (
            lambda state: f"/v1/learnings/{state['promotable_learning_id']}/promote",
            lambda state: {"target_scope": "project"},
            lambda state: [
                "learnings",
                "promote",
                str(state["promotable_learning_id"]),
                "project",
            ],
            "promote_learning",
            lambda state: {
                "learning_id": str(state["promotable_learning_id"]),
                "target_scope": "project",
            },
        ),
        (
            lambda state: f"/v1/learnings/{state['old_learning_id']}/supersede",
            lambda state: {"replaced_by": str(state["replacement_learning_id"])},
            lambda state: [
                "learnings",
                "supersede",
                str(state["old_learning_id"]),
                str(state["replacement_learning_id"]),
            ],
            "supersede_learning",
            lambda state: {
                "learning_id": str(state["old_learning_id"]),
                "replaced_by": str(state["replacement_learning_id"]),
            },
        ),
    ],
)
def test_mutation_surfaces_missing_auth_match(
    client: TestClient,
    cli_runner: CliRunner,
    session_factory: sessionmaker[Session],
    rest_path,
    rest_json,
    cli_args,
    mcp_tool,
    mcp_args,
) -> None:
    state = _seed_surface_state(session_factory)
    mcp = build_learnings_mcp(session_factory=session_factory)
    path = rest_path(state) if callable(rest_path) else rest_path

    rest_response = client.post(
        path,
        headers=_rest_headers(None, mutation=True),
        json=rest_json(state),
    )
    assert rest_response.status_code == 401

    cli_response = cli_runner.invoke(cli_app, cli_args(state))
    assert cli_response.exit_code == 1

    mcp_response = _mcp_call(mcp, mcp_tool, mcp_args(state))

    assert rest_response.json() == _cli_json(cli_response, err=True)
    assert rest_response.json() == mcp_response
