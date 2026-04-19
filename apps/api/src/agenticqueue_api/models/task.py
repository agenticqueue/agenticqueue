"""Task entity models."""

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
    jsonb_list_column,
)


class TaskModel(TimestampedSchema):
    """Pydantic schema for a task."""

    project_id: uuid.UUID
    task_type: str
    title: str
    state: str
    description: str | None = None
    contract: dict[str, Any] = Field(default_factory=dict)
    definition_of_done: list[str] = Field(default_factory=list)


class TaskRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for a task row."""

    __tablename__ = "task"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.project.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_type: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    state: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    contract: Mapped[dict[str, Any]] = jsonb_dict_column()
    definition_of_done: Mapped[list[str]] = jsonb_list_column()
