from __future__ import annotations

import socket
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import anyio
import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
import uvicorn

from agenticqueue_api.app import create_app  # noqa: E402
from agenticqueue_api.config import (  # noqa: E402
    get_psycopg_connect_args,
    get_sqlalchemy_sync_database_url,
)
from agenticqueue_api.models import CapabilityKey, CapabilityRecord  # noqa: E402

TransportCallback = Callable[[ClientSession], Awaitable[Any]]

TRUNCATE_TABLES = [
    "auth_audit_log",
    "auth_sessions",
    "auth_rate_limit",
    "project_members",
    "users",
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

MCP_INITIALIZE_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "agenticqueue-pytest", "version": "0.1.0"},
    },
}
MCP_JSON_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


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
    app: FastAPI,
    callback: TransportCallback,
    *,
    auth_token: str,
) -> Any:
    headers = {"Authorization": f"Bearer {auth_token}"}
    with serve_app(app) as base_url:
        async with streamablehttp_client(
            f"{base_url}/mcp",
            headers=headers,
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await callback(session)


def run_transport(
    app: FastAPI,
    callback: TransportCallback,
    *,
    auth_token: str,
) -> Any:
    async def _runner() -> Any:
        return await _run_transport_async(
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
            return httpx.Response(200, content=text).json()
        except ValueError:
            continue

    raise AssertionError("Expected one structured MCP tool payload")


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    engine = sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        future=True,
        connect_args=get_psycopg_connect_args(),
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> Iterator[None]:
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
    yield


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def mcp_app(session_factory: sessionmaker[Session]) -> FastAPI:
    return create_app(session_factory=session_factory)


@pytest.fixture
def client(mcp_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(mcp_app) as test_client:
        yield test_client


def _create_agent_token(client: TestClient, *, name: str = "codex") -> dict[str, Any]:
    bootstrap_response = client.post(
        "/api/auth/bootstrap_admin",
        json={"email": "admin@localhost", "password": "CorrectHorse12!"},
    )
    assert bootstrap_response.status_code == 200
    session_cookie = bootstrap_response.cookies.get("aq_session")
    assert session_cookie is not None
    client.cookies.set("aq_session", session_cookie)

    token_response = client.post("/api/auth/tokens", json={"name": name})
    assert token_response.status_code == 200
    payload = token_response.json()
    assert payload["name"] == name
    assert payload["token"].startswith("aq_live_")
    return payload


def _mcp_status(
    app: FastAPI,
    *,
    transport: str,
    token: str | None,
) -> int:
    headers = dict(MCP_JSON_HEADERS)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    with serve_app(app) as base_url:
        with httpx.Client(timeout=5.0) as http_client:
            if transport == "http":
                response = http_client.post(
                    f"{base_url}/mcp",
                    headers=headers,
                    json=MCP_INITIALIZE_BODY,
                    follow_redirects=True,
                )
                return response.status_code

            if transport == "sse":
                with http_client.stream(
                    "GET",
                    f"{base_url}/mcp/sse/",
                    headers=headers,
                ) as response:
                    return response.status_code

    raise AssertionError(f"Unsupported MCP transport: {transport}")


def test_external_client_token_created_via_browser_route_calls_list_jobs(
    client: TestClient,
    mcp_app: FastAPI,
) -> None:
    created = _create_agent_token(client, name="codex")

    async def _call_list_jobs(session) -> Any:
        return await session.call_tool(
            "list_jobs",
            {
                "token": created["token"],
                "limit": 5,
            },
        )

    result = run_transport(
        mcp_app,
        _call_list_jobs,
        auth_token=created["token"],
    )
    payload = tool_result_payload(result)

    assert result.isError is False
    assert "items" in payload


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_auth_unauthenticated(mcp_app: FastAPI, transport: str) -> None:
    assert _mcp_status(mcp_app, transport=transport, token=None) == 401


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_auth_revoked(
    client: TestClient,
    mcp_app: FastAPI,
    transport: str,
) -> None:
    created = _create_agent_token(client, name="codex")

    revoke_response = client.post(
        f"/v1/auth/tokens/{created['id']}/revoke",
        headers={
            "Authorization": f"Bearer {created['token']}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    assert revoke_response.status_code == 200
    assert revoke_response.json()["revoked_at"] is not None

    assert _mcp_status(mcp_app, transport=transport, token=created["token"]) == 403


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_auth_valid(
    client: TestClient,
    mcp_app: FastAPI,
    transport: str,
) -> None:
    created = _create_agent_token(client, name="codex")

    assert _mcp_status(mcp_app, transport=transport, token=created["token"]) == 200
