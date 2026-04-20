"""Audit log entity models."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import (
    CreatedSchema,
    CreatedTable,
    IdentifiedTable,
)


class AuditLogModel(CreatedSchema):
    """Pydantic schema for an audit log row."""

    actor_id: uuid.UUID | None = None
    entity_type: str
    entity_id: uuid.UUID | None = None
    action: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    trace_id: str | None = None
    redaction: dict[str, Any] | None = None


class AuditLogRecord(IdentifiedTable, CreatedTable, Base):
    """SQLAlchemy model for an audit log row."""

    __tablename__ = "audit_log"

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.actor.id", ondelete="SET NULL"),
        nullable=True,
    )
    entity_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    action: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    redaction: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
