"""Audit-log event hooks and request-scoped audit context."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, cast

import sqlalchemy as sa
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session, object_session

from agenticqueue_api.db import Base
from agenticqueue_api.models.audit_log import AuditLogRecord

AUDIT_ACTOR_ID_KEY = "agenticqueue_audit_actor_id"
AUDIT_TRACE_ID_KEY = "agenticqueue_audit_trace_id"
_AUDIT_BEFORE_KEY = "agenticqueue_audit_before"


def set_session_audit_context(
    session: Session,
    *,
    actor_id: uuid.UUID | None,
    trace_id: str | None,
) -> None:
    """Attach request-scoped audit context to a SQLAlchemy session."""

    session.info[AUDIT_ACTOR_ID_KEY] = actor_id
    session.info[AUDIT_TRACE_ID_KEY] = trace_id


def _is_auditable_instance(target: object) -> bool:
    return isinstance(target, Base) and not isinstance(target, AuditLogRecord)


def _serialize_snapshot(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: jsonable_encoder(value) for key, value in row.items()}


def _load_row_snapshot(
    connection: sa.Connection,
    mapper: sa.orm.Mapper[Any],
    entity_id: uuid.UUID | None,
) -> dict[str, Any] | None:
    if entity_id is None:
        return None

    primary_key_column = mapper.primary_key[0]
    table = cast(sa.Table, mapper.local_table)
    row = (
        connection.execute(sa.select(table).where(primary_key_column == entity_id))
        .mappings()
        .one_or_none()
    )
    return _serialize_snapshot(cast(Mapping[str, Any] | None, row))


@sa.event.listens_for(Session, "before_flush")
def _capture_before_snapshots(
    session: Session,
    flush_context: Any,
    instances: Any,
) -> None:
    snapshots: dict[int, dict[str, Any] | None] = session.info.setdefault(
        _AUDIT_BEFORE_KEY, {}
    )
    connection = session.connection()

    for target in session.dirty:
        if not _is_auditable_instance(target):
            continue
        if not session.is_modified(target, include_collections=False):
            continue

        inspection = sa.inspect(target)
        entity_id = getattr(target, inspection.mapper.primary_key[0].key, None)
        snapshots[id(target)] = _load_row_snapshot(
            connection, inspection.mapper, entity_id
        )

    for target in session.deleted:
        if not _is_auditable_instance(target):
            continue

        inspection = sa.inspect(target)
        entity_id = getattr(target, inspection.mapper.primary_key[0].key, None)
        snapshots[id(target)] = _load_row_snapshot(
            connection, inspection.mapper, entity_id
        )


@sa.event.listens_for(Session, "after_flush_postexec")
@sa.event.listens_for(Session, "after_soft_rollback")
def _clear_before_snapshots(session: Session, previous_transaction: Any) -> None:
    session.info.pop(_AUDIT_BEFORE_KEY, None)


def _pop_before_snapshot(target: object) -> dict[str, Any] | None:
    session = object_session(target)
    if session is None:
        return None

    snapshots = session.info.get(_AUDIT_BEFORE_KEY)
    if not isinstance(snapshots, dict):
        return None
    return snapshots.pop(id(target), None)


def _write_audit_row(
    mapper: sa.orm.Mapper[Any],
    connection: sa.Connection,
    target: object,
    *,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    if not _is_auditable_instance(target):
        return

    session = object_session(target)
    actor_id = None if session is None else session.info.get(AUDIT_ACTOR_ID_KEY)
    trace_id = None if session is None else session.info.get(AUDIT_TRACE_ID_KEY)
    entity_key = cast(str, mapper.primary_key[0].key)
    table = cast(sa.Table, mapper.local_table)
    entity_id = getattr(target, entity_key, None)

    connection.execute(
        sa.insert(cast(sa.Table, AuditLogRecord.__table__)).values(
            actor_id=actor_id,
            entity_type=table.name,
            entity_id=entity_id,
            action=action,
            before=before,
            after=after,
            trace_id=trace_id,
        )
    )


@sa.event.listens_for(Base, "after_insert", propagate=True)
def _audit_after_insert(
    mapper: sa.orm.Mapper[Any],
    connection: sa.Connection,
    target: object,
) -> None:
    entity_id = getattr(target, cast(str, mapper.primary_key[0].key), None)
    _write_audit_row(
        mapper,
        connection,
        target,
        action="CREATE",
        before=None,
        after=_load_row_snapshot(connection, mapper, entity_id),
    )


@sa.event.listens_for(Base, "after_update", propagate=True)
def _audit_after_update(
    mapper: sa.orm.Mapper[Any],
    connection: sa.Connection,
    target: object,
) -> None:
    entity_id = getattr(target, cast(str, mapper.primary_key[0].key), None)
    _write_audit_row(
        mapper,
        connection,
        target,
        action="UPDATE",
        before=_pop_before_snapshot(target),
        after=_load_row_snapshot(connection, mapper, entity_id),
    )


@sa.event.listens_for(Base, "after_delete", propagate=True)
def _audit_after_delete(
    mapper: sa.orm.Mapper[Any],
    connection: sa.Connection,
    target: object,
) -> None:
    _write_audit_row(
        mapper,
        connection,
        target,
        action="DELETE",
        before=_pop_before_snapshot(target),
        after=None,
    )
