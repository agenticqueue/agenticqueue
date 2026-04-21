from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
import uvicorn

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.capabilities import grant_capability
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
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

TransportName = str
TransportCallback = Callable[[ClientSession], Awaitable[Any]]

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


@dataclass(frozen=True)
class SeededTask:
    actor_id: uuid.UUID
    project_id: uuid.UUID
    task_id: uuid.UUID
    token: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _example_contract() -> dict[str, object]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _pythonpath() -> str:
    roots = [
        _repo_root() / "apps" / "api" / "src",
        _repo_root() / "apps" / "cli" / "src",
        _repo_root(),
    ]
    return os.pathsep.join(str(path) for path in roots)


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
            "id": str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/{handle}")
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


def seed_task_with_token(
    session_factory: sessionmaker[Session],
    *,
    handle: str,
    grant_query_graph: bool = True,
) -> SeededTask:
    contract = _example_contract()
    with session_factory() as session:
        actor = create_actor(session, _actor_payload(handle=handle))
        workspace = create_workspace(
            session,
            WorkspaceModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "slug": f"{handle}-workspace",
                    "name": f"{handle.title()} Workspace",
                    "description": "MCP conformance tests",
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
                    "description": "MCP conformance tests",
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
                    "title": "Compile packet over MCP",
                    "state": "queued",
                    "description": "Render one packet from the MCP surface.",
                    "contract": contract,
                    "definition_of_done": contract["dod_checklist"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        if grant_query_graph:
            grant_capability(
                session,
                actor_id=actor.id,
                capability=CapabilityKey.QUERY_GRAPH,
                scope={"project_id": str(project.id)},
                granted_by_actor_id=actor.id,
            )
        _, token = issue_api_token(
            session,
            actor_id=actor.id,
            scopes=[],
            expires_at=None,
        )
        session.commit()
        return SeededTask(
            actor_id=actor.id,
            project_id=project.id,
            task_id=task.id,
            token=token,
        )


@contextmanager
def serve_app(app: FastAPI) -> Iterator[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("Timed out waiting for uvicorn to start")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


async def _run_transport_async(
    transport: TransportName,
    app: FastAPI,
    callback: TransportCallback,
    *,
    auth_token: str | None = None,
) -> Any:
    if transport == "stdio":
        env = os.environ.copy()
        env["PYTHONPATH"] = _pythonpath()
        env["AGENTICQUEUE_MCP_TRANSPORTS"] = "stdio"
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agenticqueue_cli.mcp"],
            cwd=_repo_root(),
            env=env,
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await callback(session)

    headers = (
        {"Authorization": f"Bearer {auth_token}"}
        if auth_token is not None and auth_token.strip()
        else None
    )
    with serve_app(app) as base_url:
        if transport == "http":
            async with streamablehttp_client(
                f"{base_url}/mcp",
                headers=headers,
            ) as (read, write, _get_session_id):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await callback(session)
        if transport == "sse":
            async with sse_client(
                f"{base_url}/mcp/sse/",
                headers=headers,
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await callback(session)
    raise ValueError(f"Unsupported MCP transport: {transport}")


def run_transport(
    transport: TransportName,
    app: FastAPI,
    callback: TransportCallback,
    *,
    auth_token: str | None = None,
) -> Any:
    async def _runner() -> Any:
        return await _run_transport_async(
            transport,
            app,
            callback,
            auth_token=auth_token,
        )

    return anyio.run(_runner)


def tool_result_payload(result: CallToolResult) -> dict[str, Any]:
    if isinstance(result.structuredContent, dict):
        return result.structuredContent

    for item in result.content:
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload

    raise AssertionError("Expected one structured MCP tool payload")


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
def mcp_app(session_factory: sessionmaker[Session]) -> FastAPI:
    return create_app(session_factory=session_factory)


@pytest.fixture(params=("stdio", "http", "sse"))
def transport(request: pytest.FixtureRequest) -> TransportName:
    return str(request.param)


@pytest.fixture
def seeded_task(session_factory: sessionmaker[Session]) -> SeededTask:
    return seed_task_with_token(
        session_factory,
        handle="mcp-conformance",
        grant_query_graph=True,
    )


@pytest.fixture
def run_transport_session(
    transport: TransportName,
    mcp_app: FastAPI,
) -> Callable[[TransportCallback, str | None], Any]:
    def _run(callback: TransportCallback, auth_token: str | None = None) -> Any:
        return run_transport(
            transport,
            mcp_app,
            callback,
            auth_token=auth_token,
        )

    return _run
