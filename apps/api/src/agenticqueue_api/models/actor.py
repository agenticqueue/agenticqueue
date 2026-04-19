"""Actor entity models."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import (
    IdentifiedTable,
    TimestampedSchema,
    TimestampedTable,
)


class ActorModel(TimestampedSchema):
    """Pydantic schema for an actor."""

    handle: str
    actor_type: str
    display_name: str
    auth_subject: str | None = None
    is_active: bool


class ActorRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for an actor row."""

    __tablename__ = "actor"

    handle: Mapped[str] = mapped_column(sa.String(120), nullable=False, unique=True)
    actor_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    auth_subject: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        default=True,
        server_default=sa.true(),
    )
