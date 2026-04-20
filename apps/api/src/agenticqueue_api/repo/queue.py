"""Atomic queue claim/release/reclaim helpers backed by Postgres row locks."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from agenticqueue_api.models import TaskModel, TaskRecord

DEFAULT_CLAIM_STATES = ("todo", "queued")
DEFAULT_ACTIVE_STATES = ("claimed", "in_progress")


def _normalized_values(
    values: Sequence[str] | None,
    *,
    fallback: tuple[str, ...],
) -> tuple[str, ...]:
    if values is None:
        return fallback
    normalized = tuple(
        value.strip() for value in values if isinstance(value, str) and value.strip()
    )
    return normalized or fallback


def _normalized_labels(labels: Sequence[str] | None) -> list[str]:
    return list(dict.fromkeys(_normalized_values(labels, fallback=())))


def _task_from_row(row: Any) -> TaskModel:
    return TaskModel.model_validate(dict(row))


def _claim_next_statement(
    *,
    actor_id: uuid.UUID,
    labels: Sequence[str] | None,
    claim_states: Sequence[str] | None,
    claimed_state: str,
    claimed_at: dt.datetime | None,
    include_latency: bool,
) -> Any:
    filters: list[sa.ColumnElement[bool]] = [
        TaskRecord.state.in_(
            _normalized_values(claim_states, fallback=DEFAULT_CLAIM_STATES)
        )
    ]
    claim_labels = _normalized_labels(labels)
    if claim_labels:
        filters.append(TaskRecord.labels.contains(claim_labels))

    locked_task = (
        sa.select(TaskRecord.id)
        .where(*filters)
        .order_by(TaskRecord.priority.desc(), TaskRecord.sequence.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
        .cte("locked_task")
    )

    returning_columns = list(TaskRecord.__table__.columns)
    if include_latency:
        returning_columns.append(
            (
                sa.extract(
                    "epoch",
                    sa.func.clock_timestamp() - sa.func.statement_timestamp(),
                )
                * 1000
            ).label("claim_latency_ms")
        )

    return (
        sa.update(TaskRecord)
        .where(TaskRecord.id == locked_task.c.id)
        .values(
            state=claimed_state,
            claimed_by_actor_id=actor_id,
            claimed_at=claimed_at or dt.datetime.now(dt.UTC),
        )
        .returning(*returning_columns)
    )


def claim_next(
    session: Session,
    *,
    actor_id: uuid.UUID,
    labels: Sequence[str] | None,
    claim_states: Sequence[str] | None = None,
    claimed_state: str = "claimed",
    claimed_at: dt.datetime | None = None,
) -> TaskModel | None:
    """Atomically claim the next matching task without waiting on locked rows."""

    statement = _claim_next_statement(
        actor_id=actor_id,
        labels=labels,
        claim_states=claim_states,
        claimed_state=claimed_state,
        claimed_at=claimed_at,
        include_latency=False,
    )
    row = session.execute(statement).mappings().one_or_none()
    return None if row is None else _task_from_row(row)


def claim_next_timed(
    session: Session,
    *,
    actor_id: uuid.UUID,
    labels: Sequence[str] | None,
    claim_states: Sequence[str] | None = None,
    claimed_state: str = "claimed",
    claimed_at: dt.datetime | None = None,
) -> tuple[TaskModel | None, float | None]:
    """Claim one task and return the database-side statement latency in ms."""

    statement = _claim_next_statement(
        actor_id=actor_id,
        labels=labels,
        claim_states=claim_states,
        claimed_state=claimed_state,
        claimed_at=claimed_at,
        include_latency=True,
    )
    row = session.execute(statement).mappings().one_or_none()
    if row is None:
        return None, None
    latency_ms = float(row["claim_latency_ms"])
    payload = dict(row)
    payload.pop("claim_latency_ms", None)
    return TaskModel.model_validate(payload), latency_ms


def release_claim(
    session: Session,
    *,
    task_id: uuid.UUID,
    expected_actor_id: uuid.UUID | None = None,
    active_states: Sequence[str] | None = None,
    released_state: str = "todo",
) -> TaskModel | None:
    """Release one active claim back to a claimable state without blocking."""

    filters: list[sa.ColumnElement[bool]] = [
        TaskRecord.id == task_id,
        TaskRecord.state.in_(
            _normalized_values(active_states, fallback=DEFAULT_ACTIVE_STATES)
        ),
    ]
    if expected_actor_id is not None:
        filters.append(TaskRecord.claimed_by_actor_id == expected_actor_id)

    locked_task = (
        sa.select(TaskRecord.id)
        .where(*filters)
        .limit(1)
        .with_for_update(skip_locked=True)
        .cte("locked_task")
    )

    statement = (
        sa.update(TaskRecord)
        .where(TaskRecord.id == locked_task.c.id)
        .values(
            state=released_state,
            claimed_by_actor_id=None,
            claimed_at=None,
        )
        .returning(*TaskRecord.__table__.columns)
    )
    row = session.execute(statement).mappings().one_or_none()
    return None if row is None else _task_from_row(row)


def reclaim_claim(
    session: Session,
    *,
    task_id: uuid.UUID,
    stale_before: dt.datetime,
    active_states: Sequence[str] | None = None,
    reclaimed_state: str = "todo",
) -> TaskModel | None:
    """Reclaim a stale active task without waiting on another transaction's lock."""

    locked_task = (
        sa.select(TaskRecord.id)
        .where(
            TaskRecord.id == task_id,
            TaskRecord.state.in_(
                _normalized_values(active_states, fallback=DEFAULT_ACTIVE_STATES)
            ),
            sa.or_(
                TaskRecord.claimed_at.is_(None),
                TaskRecord.claimed_at <= stale_before,
            ),
        )
        .limit(1)
        .with_for_update(skip_locked=True)
        .cte("locked_task")
    )

    statement = (
        sa.update(TaskRecord)
        .where(TaskRecord.id == locked_task.c.id)
        .values(
            state=reclaimed_state,
            claimed_by_actor_id=None,
            claimed_at=None,
        )
        .returning(*TaskRecord.__table__.columns)
    )
    row = session.execute(statement).mappings().one_or_none()
    return None if row is None else _task_from_row(row)
