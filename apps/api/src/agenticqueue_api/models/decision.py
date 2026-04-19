"""Decision entity models."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import CreatedSchema, CreatedTable, IdentifiedTable


class DecisionModel(CreatedSchema):
    """Pydantic schema for a decision."""

    task_id: uuid.UUID
    run_id: uuid.UUID | None = None
    actor_id: uuid.UUID | None = None
    summary: str
    rationale: str | None = None
    decided_at: dt.datetime


class DecisionRecord(IdentifiedTable, CreatedTable, Base):
    """SQLAlchemy model for a decision row."""

    __tablename__ = "decision"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.task.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.run.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.actor.id", ondelete="SET NULL"),
        nullable=True,
    )
    summary: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    rationale: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    decided_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
    )
