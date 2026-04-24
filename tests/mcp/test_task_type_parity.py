from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from fastmcp import Client as FastMCPClient
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.task_type_registry import TaskTypeRegistry
from tests.entities import helpers as entity_helpers


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _task_types_dir() -> Path:
    return _repo_root() / "task_types"


def _build_temp_task_type_registry(tmp_path: Path) -> TaskTypeRegistry:
    copied_dir = tmp_path / "task_types"
    copied_dir.mkdir()
    for source in _task_types_dir().iterdir():
        shutil.copy2(source, copied_dir / source.name)
    registry = TaskTypeRegistry(copied_dir, reload_enabled=False)
    registry.load()
    return registry


def _mcp_call(server: Any, tool_name: str, arguments: dict[str, object]) -> dict[str, Any]:
    async def _invoke() -> dict[str, Any]:
        async with FastMCPClient(server) as client:
            result = await client.call_tool(tool_name, arguments)
            return result.data

    return asyncio.run(_invoke())


def _normalized_rest_payload(response) -> dict[str, Any]:
    payload = response.json()
    if isinstance(payload, dict) and isinstance(payload.get("detail"), dict):
        return payload["detail"]
    return payload


def test_get_task_type_mcp_matches_rest_for_missing_definition(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    registry = _build_temp_task_type_registry(tmp_path)
    app = create_app(session_factory=session_factory, task_type_registry=registry)
    actor = entity_helpers.seed_actor(
        session_factory,
        handle="task-type-parity-reader",
        actor_type="agent",
        display_name="Task Type Parity Reader",
    )
    token = entity_helpers.seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=["task:read"],
    )

    with TestClient(app) as client:
        rest_response = client.get(
            "/v1/task-types/missing-task-type",
            headers={"Authorization": f"Bearer {token}"},
        )

    mcp_response = _mcp_call(
        app.state.mcp_server,
        "get_task_type",
        {"name": "missing-task-type", "token": token},
    )

    assert mcp_response == _normalized_rest_payload(rest_response)


def test_update_task_type_mcp_matches_rest_for_worker_forbidden(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    registry = _build_temp_task_type_registry(tmp_path)
    app = create_app(session_factory=session_factory, task_type_registry=registry)
    actor = entity_helpers.seed_actor(
        session_factory,
        handle="task-type-parity-worker",
        actor_type="agent",
        display_name="Task Type Parity Worker",
    )
    token = entity_helpers.seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=["task:read"],
    )
    payload = {
        "schema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}},
            "required": ["repo"],
            "additionalProperties": False,
        },
        "policy": {"autonomy_tier": 2, "hitl_required": True},
    }

    with TestClient(app) as client:
        rest_response = client.patch(
            "/v1/task-types/coding-task",
            headers=entity_helpers.auth_headers(token),
            json=payload,
        )

    mcp_response = _mcp_call(
        app.state.mcp_server,
        "update_task_type",
        {
            "name": "coding-task",
            "schema": payload["schema"],
            "policy": payload["policy"],
            "token": token,
        },
    )

    assert mcp_response == _normalized_rest_payload(rest_response)
