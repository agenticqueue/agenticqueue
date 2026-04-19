"""Audit log entity models."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from pydantic import Field
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import (
    CreatedSchema,
    CreatedTable,
    IdentifiedTable,
    jsonb_dict_column,
)


class AuditLogModel(CreatedSchema):
    """Pydantic schema for an audit log row."""

    actor_id: uuid.UUID | None = None
    entity_type: str
    entity_id: uuid.UUID | None = None
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


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
    payload: Mapped[dict[str, Any]] = jsonb_dict_column()
