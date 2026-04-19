"""Policy entity models."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from pydantic import Field
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import (
    IdentifiedTable,
    TimestampedSchema,
    TimestampedTable,
    jsonb_dict_column,
)


class PolicyModel(TimestampedSchema):
    """Pydantic schema for a policy pack."""

    workspace_id: uuid.UUID | None = None
    name: str
    version: str
    hitl_required: bool
    autonomy_tier: int
    body: dict[str, Any] = Field(default_factory=dict)


class PolicyRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for a policy row."""

    __tablename__ = "policy"
    __table_args__ = (
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            "version",
            name="uq_policy_workspace_id_name_version",
        ),
    )

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.workspace.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    version: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    hitl_required: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        default=False,
        server_default=sa.false(),
    )
    autonomy_tier: Mapped[int] = mapped_column(sa.SmallInteger(), nullable=False)
    body: Mapped[dict[str, Any]] = jsonb_dict_column()
