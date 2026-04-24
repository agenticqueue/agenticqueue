"""Auth-specific audit log models with user-or-actor attribution."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import CreatedTable, IdentifiedTable


class AuthAuditLogRecord(IdentifiedTable, CreatedTable, Base):
    """SQLAlchemy model for auth boundary audit rows."""

    __tablename__ = "auth_audit_log"
    __table_args__ = (
        sa.CheckConstraint(
            "user_id IS NOT NULL OR actor_id IS NOT NULL",
            name="ck_auth_audit_log_has_subject",
        ),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.actor.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(sa.String(45), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
