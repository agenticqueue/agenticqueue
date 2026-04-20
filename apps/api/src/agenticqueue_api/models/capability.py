"""Capability catalog and grant models."""

from __future__ import annotations

import datetime as dt
import uuid
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from pydantic import Field, field_validator
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import (
    IdentifiedTable,
    TimestampedSchema,
    TimestampedTable,
    jsonb_dict_column,
)


class CapabilityKey(StrEnum):
    """Standard Phase 1 capability keys."""

    READ_REPO = "read_repo"
    WRITE_BRANCH = "write_branch"
    RUN_TESTS = "run_tests"
    QUERY_GRAPH = "query_graph"
    SEARCH_MEMORY = "search_memory"
    CREATE_ARTIFACT = "create_artifact"
    UPDATE_TASK = "update_task"
    TRIGGER_HANDOFF = "trigger_handoff"
    READ_LEARNINGS = "read_learnings"
    WRITE_LEARNING = "write_learning"
    PROMOTE_LEARNING = "promote_learning"
    ADMIN = "admin"


CAPABILITY_KEY_ENUM = sa.Enum(
    CapabilityKey,
    name="capability_key",
    native_enum=False,
    validate_strings=True,
    values_callable=lambda members: [member.value for member in members],
)


STANDARD_CAPABILITY_DESCRIPTIONS: dict[CapabilityKey, str] = {
    CapabilityKey.READ_REPO: "Read repository contents.",
    CapabilityKey.WRITE_BRANCH: "Write code changes to the repository branch.",
    CapabilityKey.RUN_TESTS: "Run verification and test commands.",
    CapabilityKey.QUERY_GRAPH: "Query graph lineage and dependency data.",
    CapabilityKey.SEARCH_MEMORY: "Search stored learnings and memory.",
    CapabilityKey.CREATE_ARTIFACT: "Create artifacts linked to task runs.",
    CapabilityKey.UPDATE_TASK: "Update task state and metadata.",
    CapabilityKey.TRIGGER_HANDOFF: "Trigger downstream handoffs or dispatches.",
    CapabilityKey.READ_LEARNINGS: "Read structured learnings.",
    CapabilityKey.WRITE_LEARNING: "Write new task or project learnings.",
    CapabilityKey.PROMOTE_LEARNING: "Promote a learning to broader scope.",
    CapabilityKey.ADMIN: "Perform privileged administrative actions.",
}


def capability_grant_is_active(
    *,
    revoked_at: dt.datetime | None,
    expires_at: dt.datetime | None,
    now: dt.datetime | None = None,
) -> bool:
    """Return whether a capability grant is active."""

    current_time = now or dt.datetime.now(dt.UTC)
    if revoked_at is not None:
        return False
    if expires_at is not None and expires_at <= current_time:
        return False
    return True


class CapabilityModel(TimestampedSchema):
    """Pydantic schema for a capability catalog row."""

    key: CapabilityKey
    description: str

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("description must not be empty")
        return normalized


class CapabilityRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for a capability catalog row."""

    __tablename__ = "capability"
    __table_args__ = (sa.UniqueConstraint("key", name="uq_capability_key"),)

    key: Mapped[CapabilityKey] = mapped_column(CAPABILITY_KEY_ENUM, nullable=False)
    description: Mapped[str] = mapped_column(sa.Text(), nullable=False)


class CapabilityGrantModel(TimestampedSchema):
    """Pydantic schema for one capability grant."""

    actor_id: uuid.UUID
    capability_id: uuid.UUID
    capability: CapabilityKey
    scope: dict[str, Any] = Field(default_factory=dict)
    granted_by_actor_id: uuid.UUID | None = None
    expires_at: dt.datetime | None = None
    revoked_at: dt.datetime | None = None

    @field_validator("scope", mode="before")
    @classmethod
    def validate_scope(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("scope must be an object")
        return dict(value)

    @property
    def is_active(self) -> bool:
        """Return whether the grant is active."""

        return capability_grant_is_active(
            revoked_at=self.revoked_at,
            expires_at=self.expires_at,
        )


class CapabilityGrantRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for a capability grant row."""

    __tablename__ = "capability_grant"
    __table_args__ = (
        sa.Index("ix_capability_grant_actor_id", "actor_id"),
        sa.Index("ix_capability_grant_capability_id", "capability_id"),
    )

    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.actor.id", ondelete="CASCADE"),
        nullable=False,
    )
    capability_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.capability.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope: Mapped[dict[str, Any]] = jsonb_dict_column()
    granted_by_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.actor.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[dt.datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
