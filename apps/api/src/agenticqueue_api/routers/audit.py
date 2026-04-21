"""Dedicated audit-log query surfaces shared by REST and MCP."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
import uuid
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import Field
from sqlalchemy.orm import Session

from agenticqueue_api.audit import set_session_audit_context
from agenticqueue_api.auth import AuthenticatedRequest, authenticate_api_token
from agenticqueue_api.errors import error_payload, raise_api_error
from agenticqueue_api.models import ActorModel, ApiTokenModel, AuditLogModel, AuditLogRecord
from agenticqueue_api.models.shared import SchemaModel
from agenticqueue_api.pagination import (
    DEFAULT_LIST_LIMIT,
    LIMIT_HEADER,
    MAX_LIST_LIMIT,
    NEXT_CURSOR_HEADER,
    coerce_cursor_value,
    decode_cursor,
    encode_cursor,
)


@dataclass(frozen=True)
class AuditSurfaceError(Exception):
    """Structured error raised by the shared audit surface helpers."""

    status_code: int
    payload: dict[str, Any]


class AuditQueryRequest(SchemaModel):
    """Input for REST and MCP audit-log queries."""

    actor_id: uuid.UUID | None = None
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    action: str | None = None
    since: dt.datetime | None = None
    until: dt.datetime | None = None
    limit: int = Field(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT)
    cursor: str | None = None


class AuditQueryResponse(SchemaModel):
    """Paginated audit-log response."""

    items: list[AuditLogModel]
    next_cursor: str | None = None


def _surface_error(
    status_code: int,
    message: str,
    *,
    details: Any = None,
) -> AuditSurfaceError:
    return AuditSurfaceError(
        status_code=status_code,
        payload=error_payload(
            status_code=status_code,
            message=message,
            details=details,
        ),
    )


def _http_error(error: AuditSurfaceError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.payload)


def _require_request_auth(request: Request) -> AuthenticatedRequest:
    actor = getattr(request.state, "actor", None)
    api_token = getattr(request.state, "api_token", None)
    if not isinstance(actor, ActorModel) or not isinstance(api_token, ApiTokenModel):
        raise_api_error(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")
    return AuthenticatedRequest(actor=actor, api_token=api_token)


def authenticate_surface_token(
    session: Session,
    *,
    token: str | None,
    trace_id: str,
) -> AuthenticatedRequest:
    """Authenticate a non-HTTP audit surface and attach audit context."""

    if token is None or not token.strip():
        raise _surface_error(
            status.HTTP_401_UNAUTHORIZED,
            "Missing Authorization header",
        )

    authenticated = authenticate_api_token(session, token.strip())
    if authenticated is None:
        raise _surface_error(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid bearer token",
        )

    set_session_audit_context(
        session,
        actor_id=authenticated.actor.id,
        trace_id=trace_id,
    )
    return authenticated


def _require_token_scope(
    authenticated: AuthenticatedRequest,
    required_scope: str,
) -> None:
    scopes = set(authenticated.api_token.scopes)
    if required_scope in scopes or "admin" in scopes:
        return

    raise _surface_error(
        status.HTTP_403_FORBIDDEN,
        "Token missing required scope",
        details={
            "required_scope": required_scope,
            "granted_scopes": authenticated.api_token.scopes,
        },
    )


def _require_admin_actor(authenticated: AuthenticatedRequest) -> None:
    if authenticated.actor.actor_type == "admin":
        return
    raise _surface_error(
        status.HTTP_403_FORBIDDEN,
        "Admin actor required",
    )


def _apply_desc_cursor_clause(
    statement: Any,
    *,
    columns: list[Any],
    cursor_values: list[Any] | None,
) -> Any:
    """Apply a lexicographic cursor filter to a descending SQL statement."""

    if cursor_values is None:
        return statement

    conditions = []
    for index, column in enumerate(columns):
        comparisons = [
            columns[offset] == cursor_values[offset] for offset in range(index)
        ]
        comparisons.append(column < cursor_values[index])
        conditions.append(sa.and_(*comparisons))
    return statement.where(sa.or_(*conditions))


def invoke_query_audit_log(
    session: Session,
    *,
    authenticated: AuthenticatedRequest,
    payload: AuditQueryRequest,
) -> AuditQueryResponse:
    """Return filtered, cursor-paginated audit rows."""

    _require_token_scope(authenticated, "audit:read")
    _require_admin_actor(authenticated)

    cursor_values = None
    if payload.cursor is not None:
        raw_values = decode_cursor(payload.cursor, expected_size=2)
        cursor_values = [
            coerce_cursor_value(raw_values[0], dt.datetime),
            coerce_cursor_value(raw_values[1], uuid.UUID),
        ]

    statement = sa.select(AuditLogRecord).order_by(
        AuditLogRecord.created_at.desc(),
        AuditLogRecord.id.desc(),
    )
    if payload.actor_id is not None:
        statement = statement.where(AuditLogRecord.actor_id == payload.actor_id)
    if payload.entity_type is not None:
        statement = statement.where(AuditLogRecord.entity_type == payload.entity_type)
    if payload.entity_id is not None:
        statement = statement.where(AuditLogRecord.entity_id == payload.entity_id)
    if payload.action is not None:
        statement = statement.where(AuditLogRecord.action == payload.action)
    if payload.since is not None:
        statement = statement.where(AuditLogRecord.created_at >= payload.since)
    if payload.until is not None:
        statement = statement.where(AuditLogRecord.created_at <= payload.until)

    statement = _apply_desc_cursor_clause(
        statement,
        columns=[AuditLogRecord.created_at, AuditLogRecord.id],
        cursor_values=cursor_values,
    )

    rows = session.scalars(statement.limit(payload.limit + 1)).all()
    items = rows[: payload.limit]
    next_cursor = None
    if len(rows) > payload.limit and items:
        last_row = items[-1]
        next_cursor = encode_cursor([last_row.created_at, last_row.id])

    return AuditQueryResponse(
        items=[AuditLogModel.model_validate(row) for row in items],
        next_cursor=next_cursor,
    )


def build_audit_router(get_db_session: Any) -> APIRouter:
    """Build the Phase 9 audit-log query router."""

    router = APIRouter()

    @router.get(
        "/audit",
        include_in_schema=False,
        response_model=AuditQueryResponse,
    )
    @router.get("/v1/audit", response_model=AuditQueryResponse)
    def query_audit_log_endpoint(
        request: Request,
        response: Response,
        actor_id: uuid.UUID | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        action: str | None = None,
        since: dt.datetime | None = None,
        until: dt.datetime | None = None,
        limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
        cursor: str | None = Query(default=None),
        session: Session = Depends(get_db_session),
    ) -> AuditQueryResponse:
        authenticated = _require_request_auth(request)
        payload = AuditQueryRequest(
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )
        try:
            result = invoke_query_audit_log(
                session,
                authenticated=authenticated,
                payload=payload,
            )
        except AuditSurfaceError as error:
            raise _http_error(error) from error

        response.headers[LIMIT_HEADER] = str(limit)
        if result.next_cursor is not None:
            response.headers[NEXT_CURSOR_HEADER] = result.next_cursor
        return result

    return router


__all__ = [
    "AuditQueryRequest",
    "AuditQueryResponse",
    "AuditSurfaceError",
    "authenticate_surface_token",
    "build_audit_router",
    "invoke_query_audit_log",
]
