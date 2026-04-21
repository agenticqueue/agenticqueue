"""Task entity models."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from pydantic import Field
from pydantic import model_validator
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import (
    IdentifiedTable,
    TimestampedSchema,
    TimestampedTable,
    jsonb_dict_column,
    jsonb_list_column,
)

TASK_SEQUENCE = sa.Sequence("task_sequence_seq", schema="agenticqueue")


class TaskModel(TimestampedSchema):
    """Pydantic schema for a task."""

    project_id: uuid.UUID
    policy_id: uuid.UUID | None = None
    task_type: str
    title: str
    state: str
    priority: int = 0
    labels: list[str] = Field(default_factory=list)
    sequence: int | None = None
    claimed_by_actor_id: uuid.UUID | None = None
    claimed_at: dt.datetime | None = None
    description: str | None = None
    contract: dict[str, Any] = Field(default_factory=dict)
    definition_of_done: list[str] = Field(default_factory=list)
    attempt_count: int = Field(default=0, ge=0)
    last_failure: dict[str, Any] | None = None
    max_attempts: int = Field(default=3, ge=1)
    remaining_attempts: int = Field(default=3, ge=0)

    @model_validator(mode="after")
    def normalize_retry_fields(self) -> "TaskModel":
        self.remaining_attempts = max(self.max_attempts - self.attempt_count, 0)
        return self


class TaskRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for a task row."""

    __tablename__ = "task"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.project.id", ondelete="CASCADE"),
        nullable=False,
    )
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.policy.id", ondelete="SET NULL"),
        nullable=True,
    )
    task_type: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    state: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    priority: Mapped[int] = mapped_column(
        sa.SmallInteger(),
        nullable=False,
        default=0,
        server_default=sa.text("0"),
    )
    labels: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(sa.String(length=120)),
        nullable=False,
        default=list,
        server_default=sa.text("ARRAY[]::varchar[]"),
    )
    sequence: Mapped[int] = mapped_column(
        sa.BigInteger(),
        TASK_SEQUENCE,
        nullable=False,
        unique=True,
        server_default=TASK_SEQUENCE.next_value(),
    )
    claimed_by_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.actor.id", ondelete="SET NULL"),
        nullable=True,
    )
    claimed_at: Mapped[dt.datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    contract: Mapped[dict[str, Any]] = jsonb_dict_column()
    definition_of_done: Mapped[list[str]] = jsonb_list_column()
    attempt_count: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        default=0,
        server_default=sa.text("0"),
    )
    last_failure: Mapped[dict[str, Any] | None] = mapped_column(
        postgresql.JSONB,
        nullable=True,
    )
