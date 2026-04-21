"""AgenticQueue MCP audit tools."""

from __future__ import annotations

import datetime as dt
from typing import Any
import uuid

from fastmcp import FastMCP
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.routers.audit import (
    AuditQueryRequest,
    AuditSurfaceError,
    authenticate_surface_token,
    invoke_query_audit_log,
)


def _run_read_tool(
    session_factory: sessionmaker[Session],
    *,
    token: str | None,
    trace_name: str,
    callback,
) -> dict[str, Any]:
    with session_factory() as session:
        try:
            authenticated = authenticate_surface_token(
                session,
                token=token,
                trace_id=f"aq-mcp-{trace_name}-{uuid.uuid4()}",
            )
            payload = callback(session, authenticated)
            session.commit()
            return payload.model_dump(mode="json")
        except AuditSurfaceError as error:
            if session.in_transaction():
                session.rollback()
            return error.payload
        except Exception:
            if session.in_transaction():
                session.rollback()
            raise


def register_audit_tools(
    mcp: FastMCP,
    *,
    session_factory: sessionmaker[Session],
) -> set[str]:
    """Register audit-query tools on the shared MCP server."""

    @mcp.tool(
        name="query_audit_log",
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    def query_audit_log(
        token: str | None = None,
        actor_id: uuid.UUID | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        action: str | None = None,
        since: dt.datetime | None = None,
        until: dt.datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, object]:
        return _run_read_tool(
            session_factory,
            token=token,
            trace_name="query-audit-log",
            callback=lambda session, authenticated: invoke_query_audit_log(
                session,
                authenticated=authenticated,
                payload=AuditQueryRequest(
                    actor_id=actor_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    action=action,
                    since=since,
                    until=until,
                    limit=limit,
                    cursor=cursor,
                ),
            ),
        )

    return {"query_audit_log"}
