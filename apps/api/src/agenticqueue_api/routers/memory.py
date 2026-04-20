"""REST, CLI, and MCP memory surfaces for AQ-86."""

from __future__ import annotations

from dataclasses import dataclass
import uuid
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import Field, StringConstraints, field_validator
from sqlalchemy.orm import Session

from agenticqueue_api.audit import set_session_audit_context
from agenticqueue_api.auth import AuthenticatedRequest, authenticate_api_token
from agenticqueue_api.capabilities import ensure_actor_has_capability
from agenticqueue_api.db import write_timeout
from agenticqueue_api.errors import error_payload, raise_api_error
from agenticqueue_api.memory import MemoryItemRecord, MemoryLayer, MemorySyncService
from agenticqueue_api.models import (
    ActorModel,
    ApiTokenModel,
    AuditLogRecord,
    CapabilityKey,
    LearningModel,
)
from agenticqueue_api.models.shared import SchemaModel
from agenticqueue_api.retrieval import (
    RetrievalScope,
    RetrievalSearchQuery,
    RetrievalService,
)

ShortQueryText = StringConstraints(strict=True, min_length=1, max_length=255)
MEMORY_SEARCH_ACTION = "MEMORY_SEARCH"
MEMORY_SYNC_ACTION = "MEMORY_SYNC"
MEMORY_STATS_ACTION = "MEMORY_STATS"


@dataclass(frozen=True)
class MemorySurfaceError(Exception):
    """Structured error raised by the shared memory surface helpers."""

    status_code: int
    payload: dict[str, Any]


class MemorySearchScope(SchemaModel):
    """Optional filters for `search_memory`."""

    project_id: uuid.UUID | None = None
    surface_area: list[str] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)
    learning_types: list[str] = Field(default_factory=list)
    max_age_days: int | None = Field(default=None, ge=1)


class MemorySearchRequest(SchemaModel):
    """Input for `search_memory`."""

    query: Annotated[str, ShortQueryText]
    layers: list[str] = Field(default_factory=list)
    scope: MemorySearchScope | None = None
    k: int = Field(default=10, ge=1, le=25)
    fuzzy_global_search: bool = True


class MemorySearchResponse(SchemaModel):
    """Tiered retrieval response for memory search."""

    items: list[LearningModel]
    tiers_fired: list[str]


class SyncMemoryRequest(SchemaModel):
    """Input for `sync_memory`."""

    layer: MemoryLayer
    scope_id: uuid.UUID
    paths: list[str] = Field(default_factory=list)
    full_sync: bool = False

    @field_validator("paths")
    @classmethod
    def _validate_paths(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if not normalized:
            raise ValueError("paths must contain at least one file or directory")
        return normalized


class SyncMemoryResponse(SchemaModel):
    """Sync result returned by every transport."""

    layer: MemoryLayer
    scope_id: uuid.UUID
    upserted: int
    pruned: int
    full_sync: bool
    partial: bool


class MemoryStatsResponse(SchemaModel):
    """Aggregated counts for stored memory rows."""

    layer: MemoryLayer | None = None
    scope_id: uuid.UUID | None = None
    total_items: int
    by_layer: dict[str, int] = Field(default_factory=dict)


def _surface_error(
    status_code: int,
    message: str,
    *,
    details: Any = None,
) -> MemorySurfaceError:
    return MemorySurfaceError(
        status_code=status_code,
        payload=error_payload(
            status_code=status_code,
            message=message,
            details=details,
        ),
    )


def _http_error(error: MemorySurfaceError) -> HTTPException:
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
    """Authenticate a non-HTTP memory surface and attach audit context."""

    if token is None or not token.strip():
        raise _surface_error(status.HTTP_401_UNAUTHORIZED, "Missing Authorization header")

    authenticated = authenticate_api_token(session, token.strip())
    if authenticated is None:
        raise _surface_error(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")

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


def _search_scope_to_retrieval_scope(
    scope: MemorySearchScope | None,
) -> RetrievalScope:
    if scope is None:
        return RetrievalScope()
    return RetrievalScope(
        project_id=scope.project_id,
        surface_area=tuple(scope.surface_area),
        owners=tuple(scope.owners),
        learning_types=tuple(scope.learning_types),
        max_age_days=scope.max_age_days,
    )


def _memory_stats_required_scope(
    *,
    layer: MemoryLayer | None,
    scope_id: uuid.UUID | None,
) -> dict[str, str]:
    if layer is MemoryLayer.PROJECT and scope_id is not None:
        return {"project_id": str(scope_id)}
    return {}


def _insert_audit_row(
    session: Session,
    *,
    entity_id: uuid.UUID | None,
    action: str,
    after: dict[str, Any],
) -> None:
    session.execute(
        sa.insert(AuditLogRecord).values(
            actor_id=session.info.get("agenticqueue_audit_actor_id"),
            entity_type="memory",
            entity_id=entity_id,
            action=action,
            before=None,
            after=after,
            trace_id=session.info.get("agenticqueue_audit_trace_id"),
            redaction=session.info.get("agenticqueue_audit_redaction"),
        )
    )


def invoke_search_memory(
    session: Session,
    *,
    authenticated: AuthenticatedRequest,
    payload: MemorySearchRequest,
) -> MemorySearchResponse:
    """Run tiered memory search over learnings."""

    _require_token_scope(authenticated, "memory:read")
    required_scope = {}
    if payload.scope is not None and payload.scope.project_id is not None:
        required_scope = {"project_id": str(payload.scope.project_id)}
    ensure_actor_has_capability(
        session,
        actor=authenticated.actor,
        capability=CapabilityKey.SEARCH_MEMORY,
        required_scope=required_scope,
        entity_type="memory",
        entity_id=payload.scope.project_id if payload.scope else None,
    )

    query = RetrievalSearchQuery(
        query=payload.query,
        layers=tuple(payload.layers),
        scope=_search_scope_to_retrieval_scope(payload.scope),
        k=payload.k,
        fuzzy_global_search=payload.fuzzy_global_search,
    )
    with write_timeout(session, endpoint="v1.memory.search"):
        result = RetrievalService(session).search(query)
        _insert_audit_row(
            session,
            entity_id=payload.scope.project_id if payload.scope else None,
            action=MEMORY_SEARCH_ACTION,
            after={
                "query": payload.query,
                "count": len(result.items),
                "layers": list(payload.layers),
                "project_id": (
                    str(payload.scope.project_id)
                    if payload.scope and payload.scope.project_id is not None
                    else None
                ),
                "tiers_fired": list(result.tiers_fired),
            },
        )
    return MemorySearchResponse(
        items=result.items,
        tiers_fired=result.tiers_fired,
    )


def invoke_sync_memory(
    session: Session,
    *,
    authenticated: AuthenticatedRequest,
    payload: SyncMemoryRequest,
) -> SyncMemoryResponse:
    """Walk source files and sync them into `memory_item`."""

    _require_token_scope(authenticated, "memory:write")
    ensure_actor_has_capability(
        session,
        actor=authenticated.actor,
        capability=CapabilityKey.ADMIN,
        required_scope={},
        entity_type="memory",
        entity_id=payload.scope_id,
    )

    try:
        with write_timeout(session, endpoint="v1.memory.sync"):
            result = MemorySyncService(session).sync(
                layer=payload.layer,
                scope_id=payload.scope_id,
                paths=payload.paths,
                full_sync=payload.full_sync,
            )
            _insert_audit_row(
                session,
                entity_id=payload.scope_id,
                action=MEMORY_SYNC_ACTION,
                after={
                    "layer": payload.layer.value,
                    "scope_id": str(payload.scope_id),
                    "paths": list(payload.paths),
                    "upserted": result.upserted,
                    "pruned": result.pruned,
                    "full_sync": result.full_sync,
                    "partial": result.partial,
                },
            )
    except FileNotFoundError as error:
        raise _surface_error(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ValueError as error:
        raise _surface_error(status.HTTP_400_BAD_REQUEST, str(error)) from error

    return SyncMemoryResponse(
        layer=payload.layer,
        scope_id=payload.scope_id,
        upserted=result.upserted,
        pruned=result.pruned,
        full_sync=result.full_sync,
        partial=result.partial,
    )


def invoke_memory_stats(
    session: Session,
    *,
    authenticated: AuthenticatedRequest,
    layer: MemoryLayer | None = None,
    scope_id: uuid.UUID | None = None,
) -> MemoryStatsResponse:
    """Return aggregated counts for stored memory items."""

    _require_token_scope(authenticated, "memory:read")
    ensure_actor_has_capability(
        session,
        actor=authenticated.actor,
        capability=CapabilityKey.SEARCH_MEMORY,
        required_scope=_memory_stats_required_scope(layer=layer, scope_id=scope_id),
        entity_type="memory",
        entity_id=scope_id,
    )

    statement = sa.select(
        MemoryItemRecord.layer,
        sa.func.count(MemoryItemRecord.id),
    ).group_by(MemoryItemRecord.layer)
    if layer is not None:
        statement = statement.where(MemoryItemRecord.layer == layer)
    if scope_id is not None:
        statement = statement.where(MemoryItemRecord.scope_id == scope_id)

    with write_timeout(session, endpoint="v1.memory.stats"):
        rows = session.execute(statement).all()
        by_layer = {member.value: 0 for member in MemoryLayer}
        for memory_layer, count in rows:
            by_layer[memory_layer.value] = int(count)
        total_items = sum(by_layer.values())
        _insert_audit_row(
            session,
            entity_id=scope_id,
            action=MEMORY_STATS_ACTION,
            after={
                "layer": layer.value if layer is not None else None,
                "scope_id": str(scope_id) if scope_id is not None else None,
                "total_items": total_items,
                "by_layer": by_layer,
            },
        )

    return MemoryStatsResponse(
        layer=layer,
        scope_id=scope_id,
        total_items=total_items,
        by_layer=by_layer,
    )


def build_memory_router(get_db_session: Any) -> APIRouter:
    """Build the Phase 4 memory router."""

    router = APIRouter()

    @router.post(
        "/memory/search",
        include_in_schema=False,
        response_model=MemorySearchResponse,
    )
    @router.post("/v1/memory/search", response_model=MemorySearchResponse)
    def search_memory_endpoint(
        payload: MemorySearchRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> MemorySearchResponse:
        authenticated = _require_request_auth(request)
        try:
            response = invoke_search_memory(
                session,
                authenticated=authenticated,
                payload=payload,
            )
            session.commit()
            return response
        except MemorySurfaceError as error:
            if session.in_transaction():
                session.rollback()
            raise _http_error(error) from error

    @router.post(
        "/memory/sync",
        include_in_schema=False,
        response_model=SyncMemoryResponse,
    )
    @router.post("/v1/memory/sync", response_model=SyncMemoryResponse)
    def sync_memory_endpoint(
        payload: SyncMemoryRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> SyncMemoryResponse:
        authenticated = _require_request_auth(request)
        try:
            response = invoke_sync_memory(
                session,
                authenticated=authenticated,
                payload=payload,
            )
            session.commit()
            return response
        except MemorySurfaceError as error:
            if session.in_transaction():
                session.rollback()
            raise _http_error(error) from error

    @router.get(
        "/memory/stats",
        include_in_schema=False,
        response_model=MemoryStatsResponse,
    )
    @router.get("/v1/memory/stats", response_model=MemoryStatsResponse)
    def memory_stats_endpoint(
        request: Request,
        layer: Annotated[MemoryLayer | None, Query()] = None,
        scope_id: Annotated[uuid.UUID | None, Query()] = None,
        session: Session = Depends(get_db_session),
    ) -> MemoryStatsResponse:
        authenticated = _require_request_auth(request)
        try:
            response = invoke_memory_stats(
                session,
                authenticated=authenticated,
                layer=layer,
                scope_id=scope_id,
            )
            session.commit()
            return response
        except MemorySurfaceError as error:
            if session.in_transaction():
                session.rollback()
            raise _http_error(error) from error

    return router


__all__ = [
    "MEMORY_SEARCH_ACTION",
    "MEMORY_STATS_ACTION",
    "MEMORY_SYNC_ACTION",
    "MemorySearchRequest",
    "MemorySearchResponse",
    "MemorySearchScope",
    "MemoryStatsResponse",
    "MemorySurfaceError",
    "SyncMemoryRequest",
    "SyncMemoryResponse",
    "authenticate_surface_token",
    "build_memory_router",
    "invoke_memory_stats",
    "invoke_search_memory",
    "invoke_sync_memory",
]
