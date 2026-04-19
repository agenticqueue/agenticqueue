"""Learning entity models."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from pydantic import Field
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import (
    IdentifiedTable,
    TimestampedSchema,
    TimestampedTable,
    jsonb_list_column,
)


class LearningModel(TimestampedSchema):
    """Pydantic schema for a learning."""

    task_id: uuid.UUID | None = None
    owner_actor_id: uuid.UUID | None = None
    title: str
    learning_type: str
    what_happened: str
    what_learned: str
    action_rule: str
    applies_when: str
    does_not_apply_when: str
    evidence: list[str] = Field(default_factory=list)
    scope: str
    confidence: str
    status: str
    review_date: dt.date | None = None


class LearningRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for a learning row."""

    __tablename__ = "learning"

    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.task.id", ondelete="SET NULL"),
        nullable=True,
    )
    owner_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.actor.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    learning_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    what_happened: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    what_learned: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    action_rule: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    applies_when: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    does_not_apply_when: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    evidence: Mapped[list[str]] = jsonb_list_column()
    scope: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    confidence: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    review_date: Mapped[dt.date | None] = mapped_column(sa.Date(), nullable=True)
