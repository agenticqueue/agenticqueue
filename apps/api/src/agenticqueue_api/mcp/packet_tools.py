"""FastMCP packet tools for AgenticQueue."""

from __future__ import annotations

from dataclasses import dataclass
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from fastmcp import FastMCP

from agenticqueue_api.audit import set_session_audit_context
from agenticqueue_api.auth import AuthenticatedRequest, authenticate_api_token
from agenticqueue_api.capabilities import list_capabilities_for_actor
from agenticqueue_api.compiler import compile_packet as compile_task_packet
from agenticqueue_api.config import (
    get_psycopg_connect_args,
    get_sqlalchemy_sync_database_url,
)
from agenticqueue_api.db import write_timeout
from agenticqueue_api.errors import error_payload
from agenticqueue_api.models import AuditLogRecord, CapabilityKey, TaskRecord
from agenticqueue_api.routers.packets import PACKET_FETCH_ACTION

PACKET_TRACE_NAME = "compile-packet"
PACKET_CAPABILITIES = (
    CapabilityKey.READ_REPO,
    CapabilityKey.QUERY_GRAPH,
    CapabilityKey.ADMIN,
)


@dataclass(frozen=True)
class PacketSurfaceError(Exception):
    """Structured error raised by transport-agnostic packet helpers."""

    status_code: int
    payload: dict[str, Any]


def _packet_error(
    status_code: int,
    message: str,
    *,
    details: Any = None,
) -> PacketSurfaceError:
    return PacketSurfaceError(
        status_code=status_code,
        payload=error_payload(
            status_code=status_code,
            message=message,
            details=details,
        ),
    )


def _default_session_factory() -> sessionmaker[Session]:
    engine = sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        future=True,
        connect_args=get_psycopg_connect_args(),
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


def _authenticate_surface_token(
    session: Session,
    *,
    token: str | None,
) -> AuthenticatedRequest:
    if token is None or not token.strip():
        raise _packet_error(401, "Missing Authorization header")

    authenticated = authenticate_api_token(session, token.strip())
    if authenticated is None:
        raise _packet_error(401, "Invalid bearer token")

    set_session_audit_context(
        session,
        actor_id=authenticated.actor.id,
        trace_id=f"aq-mcp-{PACKET_TRACE_NAME}-{uuid.uuid4()}",
    )
    return authenticated


def _capability_covers_scope(
    grant_scope: dict[str, Any],
    required_scope: dict[str, str],
) -> bool:
    for key, required_value in required_scope.items():
        if key in grant_scope and grant_scope[key] != required_value:
            return False
    return True


def _packet_denial_details(required_scope: dict[str, str]) -> dict[str, Any]:
    return {
        "missing_capabilities": [
            CapabilityKey.READ_REPO.value,
            CapabilityKey.QUERY_GRAPH.value,
        ],
        "required_scope": dict(required_scope),
    }


def _write_capability_denial(
    session: Session,
    *,
    task_id: uuid.UUID,
    required_scope: dict[str, str],
) -> None:
    session.execute(
        sa.insert(AuditLogRecord).values(
            actor_id=session.info.get("agenticqueue_audit_actor_id"),
            entity_type="task",
            entity_id=task_id,
            action="CAPABILITY_DENIED",
            before=None,
            after=_packet_denial_details(required_scope),
            trace_id=session.info.get("agenticqueue_audit_trace_id"),
            redaction=session.info.get("agenticqueue_audit_redaction"),
        )
    )
    session.commit()


def _ensure_packet_access(
    session: Session,
    *,
    authenticated: AuthenticatedRequest,
    task: TaskRecord,
) -> None:
    if authenticated.actor.actor_type == "admin":
        return

    required_scope = {"project_id": str(task.project_id)}
    for grant in list_capabilities_for_actor(session, authenticated.actor.id):
        if grant.capability not in PACKET_CAPABILITIES:
            continue
        if _capability_covers_scope(dict(grant.scope or {}), required_scope):
            return

    _write_capability_denial(
        session,
        task_id=task.id,
        required_scope=required_scope,
    )
    raise _packet_error(
        403,
        "Capability grant required",
        details=_packet_denial_details(required_scope),
    )


def _packet_fetch_after(task: TaskRecord, packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "packet_version_id": packet["packet_version_id"],
        "project_id": str(task.project_id),
        "retrieval_tiers_used": list(packet.get("retrieval_tiers_used", [])),
    }


def _invoke_compile_packet(
    session: Session,
    *,
    authenticated: AuthenticatedRequest,
    task_id: uuid.UUID,
) -> dict[str, Any]:
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise _packet_error(404, "Task not found")

    _ensure_packet_access(session, authenticated=authenticated, task=task)

    with write_timeout(session, endpoint="mcp.compile_packet"):
        packet = compile_task_packet(session, task.id)
        session.execute(
            sa.insert(AuditLogRecord).values(
                actor_id=session.info.get("agenticqueue_audit_actor_id"),
                entity_type="task",
                entity_id=task.id,
                action=PACKET_FETCH_ACTION,
                before=None,
                after=_packet_fetch_after(task, packet),
                trace_id=session.info.get("agenticqueue_audit_trace_id"),
                redaction=session.info.get("agenticqueue_audit_redaction"),
            )
        )
    return packet


def _run_tool(
    session_factory: sessionmaker[Session],
    *,
    token: str | None,
    task_id: uuid.UUID,
) -> dict[str, Any]:
    with session_factory() as session:
        try:
            authenticated = _authenticate_surface_token(session, token=token)
            payload = _invoke_compile_packet(
                session,
                authenticated=authenticated,
                task_id=task_id,
            )
            session.commit()
            return payload
        except PacketSurfaceError as error:
            session.rollback()
            return error.payload


def build_packets_mcp(
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> FastMCP:
    """Build the FastMCP packet tool surface."""

    resolved_factory = session_factory or _default_session_factory()
    mcp = FastMCP(name="AgenticQueue Packets")

    @mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": False})
    def compile_packet(
        task_id: uuid.UUID,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Compile one task packet for an authenticated actor."""

        return _run_tool(
            resolved_factory,
            token=token,
            task_id=task_id,
        )

    return mcp


__all__ = ["build_packets_mcp"]
