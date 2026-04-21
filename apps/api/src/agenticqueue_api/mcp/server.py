"""Unified FastMCP server for the AgenticQueue canonical surface."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.config import get_reload_enabled, get_task_types_dir
from agenticqueue_api.mcp.approve_tools import register_approve_tools
from agenticqueue_api.mcp.audit_tools import register_audit_tools
from agenticqueue_api.mcp.common import (
    canonical_surface_tool_names,
    default_session_factory,
)
from agenticqueue_api.mcp.health_tools import register_health_tools
from agenticqueue_api.mcp.learnings_tools import build_learnings_mcp
from agenticqueue_api.mcp.memory_tools import build_memory_mcp
from agenticqueue_api.mcp.packet_tools import build_packets_mcp
from agenticqueue_api.mcp.submit_tools import register_submit_tools
from agenticqueue_api.task_type_registry import TaskTypeRegistry


def _default_task_type_registry() -> TaskTypeRegistry:
    registry = TaskTypeRegistry(
        get_task_types_dir(),
        reload_enabled=get_reload_enabled(),
    )
    registry.load()
    return registry


def build_agenticqueue_mcp(
    *,
    app: Any,
    session_factory: sessionmaker[Session] | None = None,
    task_type_registry: TaskTypeRegistry | None = None,
) -> FastMCP:
    """Build the shared FastMCP server for AgenticQueue."""

    resolved_session_factory = session_factory or default_session_factory()
    resolved_task_type_registry = task_type_registry or _default_task_type_registry()

    mcp = FastMCP(
        name="AgenticQueue",
        instructions="Canonical AgenticQueue MCP surface across stdio, streamable HTTP, and SSE.",
    )
    registered: set[str] = set()

    packet_server = build_packets_mcp(session_factory=resolved_session_factory)
    learnings_server = build_learnings_mcp(session_factory=resolved_session_factory)
    memory_server = build_memory_mcp(session_factory=resolved_session_factory)
    for child in (packet_server, learnings_server, memory_server):
        mcp.mount(child)
        registered.update(_mounted_tool_names(child))

    registered.update(
        register_submit_tools(
            mcp,
            app=app,
            session_factory=resolved_session_factory,
            task_type_registry=resolved_task_type_registry,
        )
    )
    registered.update(
        register_approve_tools(
            mcp,
            session_factory=resolved_session_factory,
            task_type_registry=resolved_task_type_registry,
        )
    )
    registered.update(
        register_audit_tools(
            mcp,
            session_factory=resolved_session_factory,
        )
    )
    registered.update(
        register_health_tools(
            mcp,
            app=app,
            session_factory=resolved_session_factory,
        )
    )

    missing = sorted(set(canonical_surface_tool_names()) - registered)
    if missing:
        raise RuntimeError(
            "Unified MCP server is missing canonical tools: " + ", ".join(missing)
        )

    setattr(mcp, "agenticqueue_registered_tools", frozenset(registered))
    return mcp


def _mounted_tool_names(server: FastMCP) -> set[str]:
    """Read already-registered child tools without starting a nested event loop."""

    tool_manager = getattr(server, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", None)
    if not isinstance(tools, dict):
        return set()
    return set(tools.keys())
