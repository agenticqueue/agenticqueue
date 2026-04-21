"""AgenticQueue MCP audit tools."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from fastmcp import FastMCP
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.models import AuditLogRecord
from agenticqueue_api.mcp.common import run_session_tool, serialize_model, surface_error


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
        limit: int = 50,
    ) -> dict[str, object]:
        def _callback(session: Session, authenticated) -> dict[str, object]:
            if authenticated.actor.actor_type != "admin":
                raise surface_error(403, "Admin actor required")
            statement = sa.select(AuditLogRecord).order_by(
                AuditLogRecord.created_at.desc(),
                AuditLogRecord.id.desc(),
            )
            if actor_id is not None:
                statement = statement.where(AuditLogRecord.actor_id == actor_id)
            if entity_type is not None:
                statement = statement.where(AuditLogRecord.entity_type == entity_type)
            if entity_id is not None:
                statement = statement.where(AuditLogRecord.entity_id == entity_id)
            if action is not None:
                statement = statement.where(AuditLogRecord.action == action)
            if since is not None:
                statement = statement.where(AuditLogRecord.created_at >= since)
            rows = session.scalars(statement.limit(limit)).all()
            return {"items": serialize_model(rows)}

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="query-audit-log",
            callback=_callback,
        )

    return {"query_audit_log"}
