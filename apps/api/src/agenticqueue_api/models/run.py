"""Run entity models."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from pydantic import Field
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import (
    IdentifiedTable,
    TimestampedSchema,
    TimestampedTable,
    jsonb_dict_column,
)


class RunModel(TimestampedSchema):
    """Pydantic schema for a run."""

    task_id: uuid.UUID
    actor_id: uuid.UUID | None = None
    status: str
    started_at: dt.datetime
    ended_at: dt.datetime | None = None
    summary: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RunRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for a run row."""

    __tablename__ = "run"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.task.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.actor.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
    )
    ended_at: Mapped[dt.datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    summary: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    details: Mapped[dict[str, Any]] = jsonb_dict_column()
