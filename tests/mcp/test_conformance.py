from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator, validate  # type: ignore[import-untyped]
import pytest

from agenticqueue_api.mcp.common import canonical_surface_tool_names, worker_visible_tool_names
from tests.mcp.conftest import SeededTask, run_transport, tool_result_payload


async def _list_tools(session) -> Any:
    return await session.list_tools()


def test_tool_listing_matches_canonical_surface(
    transport: str,
    mcp_app,
    seeded_task: SeededTask,
) -> None:
    result: Any = run_transport(
        transport,
        mcp_app,
        _list_tools,
        auth_token=seeded_task.token,
    )

    listed_tools = {tool.name for tool in result.tools}
    canonical_tools = set(canonical_surface_tool_names())
    worker_tools = set(worker_visible_tool_names())

    assert listed_tools == worker_tools
    assert listed_tools.issubset(canonical_tools)


def test_worker_visibility_is_a_strict_subset_of_canonical_surface() -> None:
    canonical_tools = set(canonical_surface_tool_names())
    worker_tools = set(worker_visible_tool_names())

    assert worker_tools
    assert worker_tools < canonical_tools


def test_tool_schemas_are_valid_json_schema(
    transport: str,
    mcp_app,
    seeded_task: SeededTask,
) -> None:
    result: Any = run_transport(
        transport,
        mcp_app,
        _list_tools,
        auth_token=seeded_task.token,
    )

    tools = {tool.name: tool for tool in result.tools}
    for tool in tools.values():
        Draft202012Validator.check_schema(tool.inputSchema)
        if tool.outputSchema is not None:
            Draft202012Validator.check_schema(tool.outputSchema)

    validate(
        {
            "task_id": str(seeded_task.task_id),
            "token": seeded_task.token,
        },
        tools["compile_packet"].inputSchema,
    )


@pytest.mark.parametrize("tool_name", ["compile_packet", "health_check"])
def test_tool_invocation_succeeds_across_supported_transports(
    transport: str,
    mcp_app,
    seeded_task: SeededTask,
    tool_name: str,
) -> None:
    async def _call_tool(session) -> Any:
        arguments = (
            {"task_id": str(seeded_task.task_id), "token": seeded_task.token}
            if tool_name == "compile_packet"
            else {}
        )
        return await session.call_tool(tool_name, arguments)

    result: Any = run_transport(
        transport,
        mcp_app,
        _call_tool,
        auth_token=seeded_task.token,
    )
    payload = tool_result_payload(result)

    assert result.isError is False
    if tool_name == "compile_packet":
        assert payload["task"]["id"] == str(seeded_task.task_id)
        assert payload["packet_version_id"]
    else:
        assert payload["status"] == "ok"


def test_error_shape_matches_surface_contract_across_transports(
    transport: str,
    mcp_app,
    seeded_task: SeededTask,
) -> None:
    async def _call_tool_without_token(session) -> Any:
        return await session.call_tool(
            "compile_packet",
            {"task_id": str(seeded_task.task_id)},
        )

    result: Any = run_transport(
        transport,
        mcp_app,
        _call_tool_without_token,
        auth_token=seeded_task.token,
    )
    payload = tool_result_payload(result)

    assert payload["error_code"] == "auth_failed"
    assert payload["message"] == "Missing Authorization header"
    assert payload["details"] is None
