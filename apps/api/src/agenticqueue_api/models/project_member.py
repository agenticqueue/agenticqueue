"""Project membership models for human users."""

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


class ProjectMemberModel(TimestampedSchema):
    """Pydantic schema for a user-project membership."""

    user_id: uuid.UUID
    project_id: uuid.UUID
    role: str


class ProjectMemberRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for a user-project membership row."""

    __tablename__ = "project_members"
    __table_args__ = (
        sa.UniqueConstraint(
            "user_id",
            "project_id",
            name="uq_project_members_user_id_project_id",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.project.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(sa.String(64), nullable=False)
