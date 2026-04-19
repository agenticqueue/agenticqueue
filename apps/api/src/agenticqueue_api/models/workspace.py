"""Workspace entity models."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import (
    IdentifiedTable,
    TimestampedSchema,
    TimestampedTable,
)


class WorkspaceModel(TimestampedSchema):
    """Pydantic schema for a workspace."""

    slug: str
    name: str
    description: str | None = None


class WorkspaceRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for a workspace row."""

    __tablename__ = "workspace"

    slug: Mapped[str] = mapped_column(sa.String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
