"""Cookie-backed human auth session models."""

from __future__ import annotations

import datetime as dt
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


class AuthSessionModel(TimestampedSchema):
    """Pydantic schema for a human auth session."""

    user_id: uuid.UUID
    session_token_hash: str
    csrf_token_hash: str
    expires_at: dt.datetime
    revoked_at: dt.datetime | None = None
    last_seen_at: dt.datetime | None = None


class AuthSessionRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for a human auth session row."""

    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_token_hash: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
        unique=True,
    )
    csrf_token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
