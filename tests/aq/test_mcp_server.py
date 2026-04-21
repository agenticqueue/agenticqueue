from __future__ import annotations

import asyncio
import copy
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import socket
import threading
import time
from typing import Any

from fastapi import FastAPI
from fastmcp import Client as FastMCPClient
import uvicorn

from agenticqueue_api.app import create_app
from agenticqueue_api.mcp.common import canonical_surface_tool_names
from agenticqueue_api.models import CapabilityKey
from tests.aq.test_packet_mcp import (
    _example_contract,
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


def _write_submission_artifacts(artifact_root: Path) -> None:
    diff_path = artifact_root / "artifacts" / "diffs" / "aq-52.patch"
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.write_text(
        "@@ /v1/tasks/{id}\n+ test_get_task_returns_200\n",
        encoding="utf-8",
    )
    test_path = artifact_root / "artifacts" / "tests" / "aq-52-pytest.txt"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(
        "test_get_task_returns_200\ntest_missing_task_returns_404\n4 passed in 0.15s\n",
        encoding="utf-8",
    )


def _valid_submission_payload() -> dict[str, Any]:
    contract = _example_contract()
    return {
        "output": copy.deepcopy(contract["output"]),
        "dod_results": [
            {"item": item, "checked": True} for item in contract["dod_checklist"]
        ],
        "had_failure": False,
        "had_block": False,
        "had_retry": False,
    }


def test_build_agenticqueue_mcp_registers_every_canonical_tool(session_factory) -> None:
    app = create_app(session_factory=session_factory)

    canonical_tools = canonical_surface_tool_names()
    server_tools = set(asyncio.run(app.state.mcp_server.get_tools()).keys())

    assert len(canonical_tools) >= 48
    assert set(canonical_tools).issubset(server_tools)


def test_create_app_succeeds_inside_running_event_loop(session_factory) -> None:
    async def _build() -> frozenset[str]:
        app = create_app(session_factory=session_factory)
        return app.state.mcp_server.agenticqueue_registered_tools

    registered_tools = asyncio.run(_build())

    assert "compile_packet" in registered_tools
    assert "search_learnings" in registered_tools


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


def test_submit_payload_accepts_valid_submission(
    session_factory,
    tmp_path: Path,
) -> None:
    _write_submission_artifacts(tmp_path)
    app = create_app(session_factory=session_factory, artifact_root=tmp_path)
    _, _, task_id, token = _seed_task_with_token(
        session_factory,
        handle="unified-mcp-submit",
        grant_capabilities=(
            CapabilityKey.RUN_TESTS,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
        ),
        task_state="in_progress",
        claimed_by_seed_actor=True,
        contract=_example_contract(),
    )

    response = _in_memory_mcp_call(
        app.state.mcp_server,
        "submit_payload",
        {
            "job_id": str(task_id),
            "payload": _valid_submission_payload(),
            "token": token,
        },
    )

    assert response["task"]["id"] == str(task_id)
    assert response["task"]["state"] == "validated"
    assert response["task"]["claimed_by_actor_id"] is None
    assert response["run"]["status"] == "validated"
    assert response["next_action"] == "await_human_approval"
    assert [item["state"] for item in response["transitions"]] == [
        "submitted",
        "validated",
    ]
    assert len(response["artifacts"]) == 2
    assert len(response["learning_drafts"]) == 1
