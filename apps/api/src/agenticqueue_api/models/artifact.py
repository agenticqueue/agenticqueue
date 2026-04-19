"""Artifact entity models."""

from __future__ import annotations

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


class ArtifactModel(TimestampedSchema):
    """Pydantic schema for an artifact."""

    task_id: uuid.UUID
    run_id: uuid.UUID | None = None
    kind: str
    uri: str
    details: dict[str, Any] = Field(default_factory=dict)


class ArtifactRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for an artifact row."""

    __tablename__ = "artifact"

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
    kind: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    uri: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    details: Mapped[dict[str, Any]] = jsonb_dict_column()
