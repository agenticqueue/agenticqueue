"""Project entity models."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import (
    IdentifiedTable,
    TimestampedSchema,
    TimestampedTable,
)


class ProjectModel(TimestampedSchema):
    """Pydantic schema for a project."""

    workspace_id: uuid.UUID
    slug: str
    name: str
    description: str | None = None


class ProjectRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for a project row."""

    __tablename__ = "project"
    __table_args__ = (
        sa.UniqueConstraint(
            "workspace_id",
            "slug",
            name="uq_project_workspace_id_slug",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
