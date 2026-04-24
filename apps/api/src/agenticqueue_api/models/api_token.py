"""API token entity models."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import (
    IdentifiedTable,
    TimestampedSchema,
    TimestampedTable,
    jsonb_list_column,
)


class ApiTokenModel(TimestampedSchema):
    """Pydantic schema for an API token row."""

    token_hash: str
    actor_id: uuid.UUID
    scopes: list[str]
    expires_at: dt.datetime | None = None
    revoked_at: dt.datetime | None = None


class ApiTokenRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for an API token row."""

    __tablename__ = "api_token"

    token_hash: Mapped[str] = mapped_column(sa.Text(), nullable=False, unique=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("agenticqueue.actor.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scopes: Mapped[list[str]] = jsonb_list_column()
    expires_at: Mapped[dt.datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
