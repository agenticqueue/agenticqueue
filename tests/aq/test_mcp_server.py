from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import socket
import threading
import time
from typing import Any

from fastapi import FastAPI
from fastmcp import Client as FastMCPClient
import uvicorn

from agenticqueue_api.app import create_app
from agenticqueue_api.mcp.common import canonical_surface_tool_names
from tests.aq.test_packet_mcp import (
    _mcp_call as _in_memory_mcp_call,
    _seed_task_with_token,
    clean_database,
    engine,
    session_factory,
)

__all__ = ["clean_database", "engine", "session_factory"]


def _remote_mcp_call(
    transport: str,
    tool_name: str,
    arguments: dict[str, object],
    *,
    auth_token: str | None = None,
) -> dict[str, Any]:
    async def _invoke() -> dict[str, Any]:
        async with FastMCPClient(transport, auth=auth_token) as client:
            result = await client.call_tool(tool_name, arguments)
            return result.data

    return asyncio.run(_invoke())


@contextmanager
def _serve_app(app: FastAPI) -> Iterator[str]:
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


def test_build_agenticqueue_mcp_registers_every_canonical_tool(session_factory) -> None:
    app = create_app(session_factory=session_factory)

    canonical_tools = canonical_surface_tool_names()
    server_tools = set(asyncio.run(app.state.mcp_server.get_tools()).keys())

    assert len(canonical_tools) >= 48
    assert set(canonical_tools).issubset(server_tools)


def test_create_app_mounts_http_and_sse_mcp_surfaces(session_factory) -> None:
    app = create_app(session_factory=session_factory)

    mounted_paths = {route.path for route in app.routes if hasattr(route, "path")}

    assert "/mcp" in mounted_paths
    assert "/mcp/sse" in mounted_paths


def test_unified_mcp_compile_packet_requires_auth(session_factory) -> None:
    app = create_app(session_factory=session_factory)
    _, _, task_id, _ = _seed_task_with_token(
        session_factory,
        handle="unified-mcp-auth",
        grant_query_graph=True,
    )

    response = _in_memory_mcp_call(
        app.state.mcp_server,
        "compile_packet",
        {"task_id": str(task_id)},
    )

    assert response["error_code"] == "auth_failed"
    assert response["message"] == "Missing Authorization header"
    assert response["details"] is None


def test_unified_mcp_compile_packet_round_trips_over_http_and_sse(
    session_factory,
) -> None:
    app = create_app(session_factory=session_factory)
    _, _, task_id, token = _seed_task_with_token(
        session_factory,
        handle="unified-mcp-transport",
        grant_query_graph=True,
    )
    arguments: dict[str, object] = {"task_id": str(task_id), "token": token}

    in_memory_response = _in_memory_mcp_call(
        app.state.mcp_server,
        "compile_packet",
        arguments,
    )

    with _serve_app(app) as base_url:
        http_response = _remote_mcp_call(
            f"{base_url}/mcp",
            "compile_packet",
            arguments,
            auth_token=token,
        )
        sse_response = _remote_mcp_call(
            f"{base_url}/mcp/sse/",
            "compile_packet",
            arguments,
            auth_token=token,
        )

    assert http_response == in_memory_response
    assert sse_response == in_memory_response


def test_submit_payload_returns_not_implemented_error_shape(session_factory) -> None:
    app = create_app(session_factory=session_factory)
    _, _, task_id, token = _seed_task_with_token(
        session_factory,
        handle="unified-mcp-submit-placeholder",
        grant_query_graph=True,
    )

    response = _in_memory_mcp_call(
        app.state.mcp_server,
        "submit_payload",
        {
            "job_id": str(task_id),
            "payload": {
                "output": {
                    "diff_url": "artifact://diff",
                    "test_report": "artifact://tests",
                    "artifacts": [
                        {"kind": "report", "uri": "artifact://tests", "details": {}}
                    ],
                    "learnings": [],
                },
                "dod_results": [{"item": "placeholder", "checked": True}],
                "had_failure": False,
                "had_block": False,
                "had_retry": False,
            },
            "token": token,
        },
    )

    assert response["error_code"] == "not_implemented"
    assert (
        response["message"]
        == "submit_payload is not implemented yet on the MCP surface"
    )
    assert response["details"] == {
        "job_id": str(task_id),
        "payload_keys": [
            "dod_results",
            "had_block",
            "had_failure",
            "had_retry",
            "output",
        ],
    }
