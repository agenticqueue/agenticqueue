"""Postgres notification hooks for packet cache invalidation."""

from __future__ import annotations

import json
import uuid
from typing import Any

import sqlalchemy as sa

from agenticqueue_api.db import Base
from agenticqueue_api.models import (
    CapabilityGrantRecord,
    DecisionRecord,
    EdgeRecord,
    LearningRecord,
    PolicyRecord,
    TaskRecord,
)
from agenticqueue_api.packet_cache import PACKET_INVALIDATION_CHANNEL


def _project_id_for_task(
    connection: sa.Connection,
    task_id: uuid.UUID | None,
) -> uuid.UUID | None:
    if task_id is None:
        return None
    return connection.scalar(
        sa.select(TaskRecord.project_id).where(TaskRecord.id == task_id).limit(1)
    )


def _capability_project_id(target: CapabilityGrantRecord) -> uuid.UUID | None:
    project_id = target.scope.get("project_id")
    if not isinstance(project_id, str):
        return None
    try:
        return uuid.UUID(project_id)
    except ValueError:
        return None


def _payload_for_target(
    connection: sa.Connection,
    target: object,
    *,
    action: str,
) -> dict[str, Any] | None:
    project_id: uuid.UUID | None
    entity_id: uuid.UUID | None
    if isinstance(target, TaskRecord):
        project_id = target.project_id
        entity_id = target.id
    elif isinstance(target, DecisionRecord):
        project_id = _project_id_for_task(connection, target.task_id)
        entity_id = target.id
    elif isinstance(target, LearningRecord):
        project_id = _project_id_for_task(connection, target.task_id)
        entity_id = target.id
    elif isinstance(target, EdgeRecord):
        task_id = None
        if target.src_entity_type == "task":
            task_id = target.src_id
        elif target.dst_entity_type == "task":
            task_id = target.dst_id
        project_id = _project_id_for_task(connection, task_id)
        entity_id = target.id
    elif isinstance(target, CapabilityGrantRecord):
        project_id = _capability_project_id(target)
        entity_id = target.id
    elif isinstance(target, PolicyRecord):
        project_id = None
        entity_id = target.id
    else:
        return None

    return {
        "entity_type": type(target).__name__,
        "action": action,
        "entity_id": None if entity_id is None else str(entity_id),
        "project_id": None if project_id is None else str(project_id),
        "invalidate_all": project_id is None,
        "reason": f"{type(target).__name__.lower()}:{action}",
    }


def _publish_invalidation(
    mapper: sa.orm.Mapper[Any],
    connection: sa.Connection,
    target: object,
    *,
    action: str,
) -> None:
    del mapper
    payload = _payload_for_target(connection, target, action=action)
    if payload is None:
        return
    connection.execute(
        sa.text("SELECT pg_notify(:channel, :payload)"),
        {
            "channel": PACKET_INVALIDATION_CHANNEL,
            "payload": json.dumps(payload, sort_keys=True),
        },
    )


@sa.event.listens_for(Base, "after_insert", propagate=True)
def _notify_packet_cache_after_insert(
    mapper: sa.orm.Mapper[Any],
    connection: sa.Connection,
    target: object,
) -> None:
    _publish_invalidation(mapper, connection, target, action="insert")


@sa.event.listens_for(Base, "after_update", propagate=True)
def _notify_packet_cache_after_update(
    mapper: sa.orm.Mapper[Any],
    connection: sa.Connection,
    target: object,
) -> None:
    _publish_invalidation(mapper, connection, target, action="update")


@sa.event.listens_for(Base, "after_delete", propagate=True)
def _notify_packet_cache_after_delete(
    mapper: sa.orm.Mapper[Any],
    connection: sa.Connection,
    target: object,
) -> None:
    _publish_invalidation(mapper, connection, target, action="delete")
