"""Local human user models."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import (
    IdentifiedTable,
    TimestampedSchema,
    TimestampedTable,
)


class UserModel(TimestampedSchema):
    """Pydantic schema for a local human user."""

    email: str
    passcode_hash: str
    actor_id: uuid.UUID | None = None
    is_admin: bool
    is_active: bool


class UserRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for a local human user row."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(sa.String(320), nullable=False, unique=True)
    passcode_hash: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("agenticqueue.actor.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_admin: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        default=False,
        server_default=sa.false(),
    )
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        default=True,
        server_default=sa.true(),
    )
