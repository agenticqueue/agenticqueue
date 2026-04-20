"""Packet fetch REST surface for Phase 3 compiled context delivery."""

from __future__ import annotations

import uuid
from typing import Any, cast

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from agenticqueue_api.capabilities import ensure_actor_has_capability
from agenticqueue_api.compiler import PacketV1, compile_packet
from agenticqueue_api.db import write_timeout
from agenticqueue_api.errors import raise_api_error
from agenticqueue_api.models import ActorModel, AuditLogRecord, CapabilityKey, TaskRecord

PACKET_FETCH_CACHE_CONTROL = "private, no-store"
PACKET_FETCH_ACTION = "PACKET_FETCH"


def _packet_fetch_after(task: TaskRecord, packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "packet_version_id": packet["packet_version_id"],
        "project_id": str(task.project_id),
        "retrieval_tiers_used": list(packet.get("retrieval_tiers_used", [])),
    }


def build_packets_router(get_db_session: Any) -> APIRouter:
    """Build the dedicated packet REST surface."""

    router = APIRouter()

    @router.get(
        "/tasks/{task_id}/packet",
        include_in_schema=False,
        response_model=PacketV1,
    )
    @router.get(
        "/v1/tasks/{task_id}/packet",
        response_model=PacketV1,
    )
    def get_task_packet_endpoint(
        task_id: uuid.UUID,
        request: Request,
        response: Response,
        session: Session = Depends(get_db_session),
    ) -> PacketV1:
        task = session.get(TaskRecord, task_id)
        if task is None:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Task not found")

        actor = cast(ActorModel | None, getattr(request.state, "actor", None))
        ensure_actor_has_capability(
            session,
            actor=actor,
            capability=CapabilityKey.QUERY_GRAPH,
            required_scope={"project_id": str(task.project_id)},
            entity_type="task",
            entity_id=task.id,
        )

        with write_timeout(session, endpoint="v1.tasks.packet"):
            packet = compile_packet(session, task.id)
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

        response.headers["X-Packet-Version"] = packet["packet_version_id"]
        response.headers["Cache-Control"] = PACKET_FETCH_CACHE_CONTROL
        return PacketV1.model_validate(packet)

    return router


__all__ = [
    "PACKET_FETCH_ACTION",
    "PACKET_FETCH_CACHE_CONTROL",
    "build_packets_router",
]
