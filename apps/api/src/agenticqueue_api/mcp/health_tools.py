"""AgenticQueue MCP health and stats tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.config import get_mcp_http_port, get_mcp_transports
from agenticqueue_api.mcp.common import run_session_tool
from agenticqueue_api.middleware.idempotency import get_idempotency_stats


def _packet_cache_stats(app: Any) -> dict[str, Any]:
    packet_cache = getattr(app.state, "packet_cache", None)
    if packet_cache is None:
        return {"enabled": False}

    stats = packet_cache.stats()
    return {
        "enabled": True,
        "hits": stats.hits,
        "misses": stats.misses,
        "hit_rate": stats.hit_rate,
        "miss_reasons": stats.miss_reasons,
        "invalidations": stats.invalidations,
        "listener_error": packet_cache.listener_error,
    }


def register_health_tools(
    mcp: FastMCP,
    *,
    app: Any,
    session_factory: sessionmaker[Session],
) -> set[str]:
    """Register system-health tools on the shared MCP server."""

    registered: set[str] = set()

    @mcp.tool(name="health_check", annotations={"readOnlyHint": True, "openWorldHint": False})
    def health_check() -> dict[str, Any]:
        database_status = "ok"
        detail: str | None = None
        try:
            with session_factory() as session:
                session.execute(sa.select(sa.literal(True))).scalar_one()
        except Exception as error:
            database_status = "error"
            detail = str(error)

        status = "ok" if database_status == "ok" else "degraded"
        transports = list(get_mcp_transports())
        http_enabled = "http" in transports or "sse" in transports
        return {
            "status": status,
            "database": database_status,
            "detail": detail,
            "transports": transports,
            "http_port": get_mcp_http_port() if http_enabled else None,
            "packet_cache": _packet_cache_stats(app),
        }

    registered.add("health_check")

    @mcp.tool(name="get_stats", annotations={"readOnlyHint": True, "openWorldHint": False})
    def get_stats(token: str | None = None) -> dict[str, Any]:
        def _callback(session: Session, authenticated) -> dict[str, Any]:
            del authenticated
            idempotency = get_idempotency_stats(session)
            mcp_server = getattr(app.state, "mcp_server", None)
            registered_tools = (
                None
                if mcp_server is None
                else getattr(mcp_server, "agenticqueue_registered_tools", None)
            )
            tool_count = None if registered_tools is None else len(registered_tools)
            return {
                "idempotency": {
                    "hit_count": idempotency.hit_count,
                    "row_count": idempotency.row_count,
                    "expired_count": idempotency.expired_count,
                    "active_count": idempotency.active_count,
                },
                "packet_cache": _packet_cache_stats(app),
                "mcp": {
                    "tool_count": tool_count,
                    "transports": list(get_mcp_transports()),
                    "http_port": get_mcp_http_port(),
                },
            }

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="get-stats",
            callback=_callback,
        )

    registered.add("get_stats")

    return registered
