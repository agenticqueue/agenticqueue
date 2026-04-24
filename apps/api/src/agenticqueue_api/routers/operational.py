"""Operational read-only routes kept separate from app composition wiring."""

from __future__ import annotations

from typing import Any
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, FastAPI, Request, status
from pydantic import Field
from sqlalchemy.orm import Session

from agenticqueue_api.config import get_mcp_http_port, get_mcp_transports
from agenticqueue_api.errors import raise_api_error
from agenticqueue_api.middleware.idempotency import get_idempotency_stats
from agenticqueue_api.models import ActorModel, ApiTokenModel
from agenticqueue_api.models.shared import SchemaModel


class AuditVerifyResponse(SchemaModel):
    """Verification result for the append-only audit ledger."""

    chain_length: int
    verified_count: int
    first_break_id_or_null: uuid.UUID | None = None


class IdempotencyStatsResponse(SchemaModel):
    """Current idempotency cache counters."""

    hit_count: int
    row_count: int
    expired_count: int
    active_count: int


class PacketCacheStatsResponse(SchemaModel):
    """Current compiled-packet cache counters."""

    enabled: bool
    hits: int | None = None
    misses: int | None = None
    hit_rate: float | None = None
    miss_reasons: dict[str, int] = Field(default_factory=dict)
    invalidations: int | None = None
    listener_error: str | None = None


class McpStatsResponse(SchemaModel):
    """Current MCP transport statistics."""

    tool_count: int | None = None
    transports: list[str] = Field(default_factory=list)
    http_port: int | None = None


class StatsResponse(SchemaModel):
    """System stats exposed over the REST surface."""

    idempotency: IdempotencyStatsResponse
    packet_cache: PacketCacheStatsResponse
    mcp: McpStatsResponse


def _require_actor(request: Request) -> ActorModel:
    actor = getattr(request.state, "actor", None)
    api_token = getattr(request.state, "api_token", None)
    if not isinstance(actor, ActorModel) or not isinstance(api_token, ApiTokenModel):
        raise_api_error(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")
    return actor


def _require_admin_actor(request: Request) -> ActorModel:
    actor = _require_actor(request)
    if actor.actor_type != "admin":
        raise_api_error(status.HTTP_403_FORBIDDEN, "Admin actor required")
    return actor


def _packet_cache_stats_response(app: FastAPI) -> PacketCacheStatsResponse:
    packet_cache = getattr(app.state, "packet_cache", None)
    if packet_cache is None:
        return PacketCacheStatsResponse(enabled=False)

    stats = packet_cache.stats()
    return PacketCacheStatsResponse(
        enabled=True,
        hits=stats.hits,
        misses=stats.misses,
        hit_rate=stats.hit_rate,
        miss_reasons=stats.miss_reasons,
        invalidations=stats.invalidations,
        listener_error=(
            None
            if packet_cache.listener_error is None
            else str(packet_cache.listener_error)
        ),
    )


def build_operational_router(app: FastAPI, get_db_session: Any) -> APIRouter:
    """Build the health/stats/audit-verify surface away from app.py."""

    router = APIRouter()

    @router.get("/healthz")
    @router.get("/api/healthz", include_in_schema=False)
    @router.get("/health", include_in_schema=False)
    @router.get("/v1/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "version": app.version,
        }

    @router.get("/stats", response_model=StatsResponse)
    def stats(
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> StatsResponse:
        _require_actor(request)
        idempotency = get_idempotency_stats(session)
        mcp_server = getattr(app.state, "mcp_server", None)
        registered_tools = (
            None
            if mcp_server is None
            else getattr(mcp_server, "agenticqueue_registered_tools", None)
        )
        return StatsResponse(
            idempotency=IdempotencyStatsResponse(
                hit_count=idempotency.hit_count,
                row_count=idempotency.row_count,
                expired_count=idempotency.expired_count,
                active_count=idempotency.active_count,
            ),
            packet_cache=_packet_cache_stats_response(app),
            mcp=McpStatsResponse(
                tool_count=(
                    None if registered_tools is None else len(registered_tools)
                ),
                transports=list(get_mcp_transports()),
                http_port=get_mcp_http_port(),
            ),
        )

    @router.get(
        "/audit/verify",
        include_in_schema=False,
        response_model=AuditVerifyResponse,
    )
    @router.get("/v1/audit/verify", response_model=AuditVerifyResponse)
    def verify_audit_log_chain(
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> AuditVerifyResponse:
        _require_admin_actor(request)
        query = sa.text("""
            SELECT
              chain_length,
              verified_count,
              first_break_id_or_null
            FROM agenticqueue.verify_audit_log_chain()
            """)
        report = session.execute(query).mappings().one()
        return AuditVerifyResponse.model_validate(dict(report))

    return router


__all__ = ["AuditVerifyResponse", "StatsResponse", "build_operational_router"]
