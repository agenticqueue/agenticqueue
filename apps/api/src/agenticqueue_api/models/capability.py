"""Capability entity models."""

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


class CapabilityModel(TimestampedSchema):
    """Pydantic schema for a capability grant."""

    actor_id: uuid.UUID
    capability_key: str
    scope: str
    granted_by_actor_id: uuid.UUID | None = None
    is_active: bool


class CapabilityRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for a capability row."""

    __tablename__ = "capability"

    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.actor.id", ondelete="CASCADE"),
        nullable=False,
    )
    capability_key: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    scope: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    granted_by_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.actor.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        default=True,
        server_default=sa.true(),
    )
