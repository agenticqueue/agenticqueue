"""Unified MCP list-tools visibility filtering."""

from __future__ import annotations

from collections.abc import Sequence
import os

from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool
import mcp.types as mt

from agenticqueue_api.mcp.common import McpToolProfile, visible_tool_names

PROFILE_ENV = "AGENTICQUEUE_MCP_PROFILE"
PROFILE_HEADER = "x-agenticqueue-mcp-profile"


def _requested_profile(
    context: MiddlewareContext[mt.ListToolsRequest],
) -> McpToolProfile:
    header_value: str | None = None
    if context.fastmcp_context is not None:
        try:
            request = get_http_request()
        except Exception:
            request = None
        if request is not None:
            header_value = request.headers.get(PROFILE_HEADER)

    raw_value = (header_value or os.getenv(PROFILE_ENV) or "").strip().lower()
    if not raw_value:
        return McpToolProfile.WORKER

    try:
        return McpToolProfile(raw_value)
    except ValueError:
        return McpToolProfile.WORKER


class AgenticQueueToolVisibilityMiddleware(Middleware):
    """Filter the unified `tools/list` response to the requested profile."""

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        tools = await call_next(context)
        allowed = set(visible_tool_names(_requested_profile(context)))
        return [tool for tool in tools if tool.name in allowed]
