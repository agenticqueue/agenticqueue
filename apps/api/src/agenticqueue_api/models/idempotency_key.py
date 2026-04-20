"""Idempotency cache row for mutating API requests."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import BYTEA, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import CreatedTable


class IdempotencyKeyRecord(CreatedTable, Base):
    """SQLAlchemy model for cached idempotent request responses."""

    __tablename__ = "idempotency_key"

    key: Mapped[str] = mapped_column(sa.Text(), primary_key=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.actor.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    body_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    response_status: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    response_body: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    replay_count: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
        default=0,
    )
    expires_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        index=True,
    )
