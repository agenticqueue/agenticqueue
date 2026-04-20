"""Policy entity models."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from pydantic import Field
from pydantic import field_validator
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.capability_keys import CapabilityKey
from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import (
    IdentifiedTable,
    TimestampedSchema,
    TimestampedTable,
    jsonb_dict_column,
    jsonb_list_column,
)


class PolicyModel(TimestampedSchema):
    """Pydantic schema for a policy pack."""

    workspace_id: uuid.UUID | None = None
    name: str
    version: str
    hitl_required: bool
    autonomy_tier: int
    capabilities: list[CapabilityKey] = Field(default_factory=list)
    body: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "version")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("policy name and version must not be empty")
        return normalized

    @field_validator("autonomy_tier")
    @classmethod
    def validate_autonomy_tier(cls, value: int) -> int:
        if value < 1 or value > 5:
            raise ValueError("autonomy_tier must be between 1 and 5")
        return value

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(
        cls, values: list[CapabilityKey]
    ) -> list[CapabilityKey]:
        deduped: list[CapabilityKey] = []
        seen: set[CapabilityKey] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            deduped.append(value)
        return deduped

    @field_validator("body", mode="before")
    @classmethod
    def validate_body(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("body must be an object")
        return dict(value)


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
    capabilities: Mapped[list[str]] = jsonb_list_column()
    body: Mapped[dict[str, Any]] = jsonb_dict_column()
