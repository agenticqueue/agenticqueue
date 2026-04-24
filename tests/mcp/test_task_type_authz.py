from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from fastmcp import Client as FastMCPClient
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.mcp.common import worker_visible_tool_names
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


def test_worker_visibility_keeps_task_type_read_but_hides_mutation_tools() -> None:
    worker_tools = set(worker_visible_tool_names())

    assert "get_task_type" in worker_tools
    assert "register_task_type" not in worker_tools
    assert "update_task_type" not in worker_tools


def test_worker_token_can_read_but_not_mutate_task_types(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    registry = _build_temp_task_type_registry(tmp_path)
    app = create_app(session_factory=session_factory, task_type_registry=registry)
    actor = entity_helpers.seed_actor(
        session_factory,
        handle="task-type-authz-worker",
        actor_type="agent",
        display_name="Task Type Authz Worker",
    )
    token = entity_helpers.seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=["task:read"],
    )

    get_response = _mcp_call(
        app.state.mcp_server,
        "get_task_type",
        {"name": "coding-task", "token": token},
    )
    update_response = _mcp_call(
        app.state.mcp_server,
        "update_task_type",
        {
            "name": "coding-task",
            "schema": {
                "type": "object",
                "properties": {"repo": {"type": "string"}},
                "required": ["repo"],
                "additionalProperties": False,
            },
            "policy": {"autonomy_tier": 2, "hitl_required": True},
            "token": token,
        },
    )

    assert get_response["name"] == "coding-task"
    assert update_response["error_code"] == "forbidden"
