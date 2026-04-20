"""Role models layered on top of capability grants."""

from __future__ import annotations

import datetime as dt
import uuid
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from pydantic import Field, field_validator
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


class RoleName(StrEnum):
    """Seeded role names for the Phase 9 RBAC layer."""

    ADMIN = "admin"
    MAINTAINER = "maintainer"
    CONTRIBUTOR = "contributor"
    REVIEWER = "reviewer"
    READ_ONLY = "read-only"
    BOT = "bot"


STANDARD_ROLE_DEFINITIONS: dict[RoleName, dict[str, object]] = {
    RoleName.ADMIN: {
        "description": "Full administrative access across all AgenticQueue surfaces.",
        "capabilities": tuple(CapabilityKey),
        "scope": {},
    },
    RoleName.MAINTAINER: {
        "description": "Ship work, manage learnings, and drive handoffs without admin-only powers.",
        "capabilities": (
            CapabilityKey.READ_REPO,
            CapabilityKey.WRITE_BRANCH,
            CapabilityKey.RUN_TESTS,
            CapabilityKey.QUERY_GRAPH,
            CapabilityKey.SEARCH_MEMORY,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
            CapabilityKey.TRIGGER_HANDOFF,
            CapabilityKey.READ_LEARNINGS,
            CapabilityKey.WRITE_LEARNING,
            CapabilityKey.PROMOTE_LEARNING,
        ),
        "scope": {},
    },
    RoleName.CONTRIBUTOR: {
        "description": "Implement scoped coding work and write task-scoped learnings.",
        "capabilities": (
            CapabilityKey.READ_REPO,
            CapabilityKey.WRITE_BRANCH,
            CapabilityKey.RUN_TESTS,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
            CapabilityKey.READ_LEARNINGS,
            CapabilityKey.WRITE_LEARNING,
        ),
        "scope": {},
    },
    RoleName.REVIEWER: {
        "description": "Inspect runs, validate changes, and promote reusable learnings.",
        "capabilities": (
            CapabilityKey.READ_REPO,
            CapabilityKey.RUN_TESTS,
            CapabilityKey.QUERY_GRAPH,
            CapabilityKey.SEARCH_MEMORY,
            CapabilityKey.READ_LEARNINGS,
            CapabilityKey.PROMOTE_LEARNING,
        ),
        "scope": {},
    },
    RoleName.READ_ONLY: {
        "description": "Inspect repository, graph, and learnings state without write access.",
        "capabilities": (
            CapabilityKey.READ_REPO,
            CapabilityKey.QUERY_GRAPH,
            CapabilityKey.SEARCH_MEMORY,
            CapabilityKey.READ_LEARNINGS,
        ),
        "scope": {},
    },
    RoleName.BOT: {
        "description": "Automation-friendly bundle for repo, artifact, and task mutation work.",
        "capabilities": (
            CapabilityKey.READ_REPO,
            CapabilityKey.WRITE_BRANCH,
            CapabilityKey.RUN_TESTS,
            CapabilityKey.QUERY_GRAPH,
            CapabilityKey.SEARCH_MEMORY,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
            CapabilityKey.TRIGGER_HANDOFF,
            CapabilityKey.READ_LEARNINGS,
            CapabilityKey.WRITE_LEARNING,
        ),
        "scope": {},
    },
}


def role_assignment_is_active(
    *,
    revoked_at: dt.datetime | None,
    expires_at: dt.datetime | None,
    now: dt.datetime | None = None,
) -> bool:
    """Return whether a role assignment is currently active."""

    current_time = now or dt.datetime.now(dt.UTC)
    if revoked_at is not None:
        return False
    if expires_at is not None and expires_at <= current_time:
        return False
    return True


class RoleModel(TimestampedSchema):
    """Pydantic schema for a seeded role definition."""

    name: str
    description: str
    capabilities: list[CapabilityKey] = Field(default_factory=list)
    scope: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be empty")
        return normalized

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("description must not be empty")
        return normalized

    @field_validator("capabilities", mode="before")
    @classmethod
    def validate_capabilities(cls, value: Any) -> list[CapabilityKey]:
        if value is None:
            raise ValueError("capabilities must not be empty")
        capabilities = [CapabilityKey(item) for item in value]
        deduped = list(dict.fromkeys(capabilities))
        if not deduped:
            raise ValueError("capabilities must not be empty")
        return deduped

    @field_validator("scope", mode="before")
    @classmethod
    def validate_scope(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("scope must be an object")
        return dict(value)


class RoleRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for a seeded role row."""

    __tablename__ = "role"
    __table_args__ = (sa.UniqueConstraint("name", name="uq_role_name"),)

    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    capabilities: Mapped[list[str]] = jsonb_list_column()
    scope: Mapped[dict[str, Any]] = jsonb_dict_column()


class RoleAssignmentModel(TimestampedSchema):
    """Pydantic schema for one actor-role assignment."""

    actor_id: uuid.UUID
    role_id: uuid.UUID
    role_name: str
    description: str
    capabilities: list[CapabilityKey] = Field(default_factory=list)
    scope: dict[str, Any] = Field(default_factory=dict)
    granted_by_actor_id: uuid.UUID | None = None
    expires_at: dt.datetime | None = None
    revoked_at: dt.datetime | None = None

    @property
    def is_active(self) -> bool:
        """Return whether the assignment is currently active."""

        return role_assignment_is_active(
            revoked_at=self.revoked_at,
            expires_at=self.expires_at,
        )


class RoleAssignmentRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for one actor-role assignment row."""

    __tablename__ = "actor_role_assignment"
    __table_args__ = (
        sa.Index("ix_actor_role_assignment_actor_id", "actor_id"),
        sa.Index("ix_actor_role_assignment_role_id", "role_id"),
    )

    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.actor.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.role.id", ondelete="CASCADE"),
        nullable=False,
    )
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
