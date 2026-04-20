"""Edge entity models and active-edge helpers."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from pydantic import AliasChoices, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import CreatedSchema, CreatedTable, IdentifiedTable


class EdgeRelation(StrEnum):
    """Allowed graph edge types for Phase 1."""

    DEPENDS_ON = "depends_on"
    BLOCKS = "blocks"
    UNBLOCKS = "unblocks"
    PARALLEL = "parallel"
    GATED_BY = "gated_by"
    SUPERSEDES = "supersedes"
    INFORMED_BY = "informed_by"
    IMPLEMENTS = "implements"
    PRODUCED = "produced"
    REVIEWED_BY = "reviewed_by"
    VALIDATED_BY = "validated_by"
    TRIGGERED = "triggered"
    CONTRADICTS = "contradicts"
    DERIVED_FROM = "derived_from"
    REQUIRES_APPROVAL = "requires_approval"
    LEARNED_FROM = "learned_from"
    PARENT_OF = "parent_of"


EDGE_RELATION_ENUM = sa.Enum(
    EdgeRelation,
    name="edge_relation",
    native_enum=False,
    validate_strings=True,
    values_callable=lambda members: [member.value for member in members],
)

LEARNED_FROM_ALLOWED_ENTITY_TYPES = frozenset(
    {"task", "run", "artifact", "decision", "incident", "tool", "actor"},
)


def edge_metadata_marks_superseded(metadata: dict[str, Any] | None) -> bool:
    """Return whether edge metadata marks the edge as superseded."""

    if not metadata:
        return False

    return any(
        (
            bool(metadata.get("superseded_at")),
            bool(metadata.get("superseded_by")),
            metadata.get("status") == "superseded",
            metadata.get("is_active") is False,
        )
    )


class EdgeModel(CreatedSchema):
    """Pydantic schema for a graph edge."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    src_entity_type: str
    src_id: uuid.UUID
    dst_entity_type: str
    dst_id: uuid.UUID
    relation: EdgeRelation
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("edge_metadata", "metadata"),
    )
    created_by: uuid.UUID | None = None

    @field_validator("src_entity_type", "dst_entity_type")
    @classmethod
    def validate_entity_type(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("entity type must not be empty")
        return normalized

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("metadata must be an object")
        return dict(value)

    @model_validator(mode="after")
    def validate_relation_endpoints(self) -> EdgeModel:
        if self.relation is not EdgeRelation.LEARNED_FROM:
            return self

        src_is_learning = self.src_entity_type == "learning"
        dst_is_learning = self.dst_entity_type == "learning"
        if src_is_learning == dst_is_learning:
            raise ValueError(
                "learned_from edges must connect exactly one learning node",
            )

        related_entity_type = (
            self.dst_entity_type if src_is_learning else self.src_entity_type
        )
        if related_entity_type not in LEARNED_FROM_ALLOWED_ENTITY_TYPES:
            allowed = ", ".join(sorted(LEARNED_FROM_ALLOWED_ENTITY_TYPES))
            raise ValueError(
                "learned_from edges only support learning <-> "
                + allowed.replace(", ", " | "),
            )

        return self

    @property
    def is_active(self) -> bool:
        """Return whether the edge should appear in active graph traversals."""

        return not edge_metadata_marks_superseded(self.metadata)


class EdgeRecord(IdentifiedTable, CreatedTable, Base):
    """SQLAlchemy model for an edge row."""

    __tablename__ = "edge"
    __table_args__ = (
        sa.UniqueConstraint(
            "src_entity_type",
            "src_id",
            "dst_entity_type",
            "dst_id",
            "relation",
            name="uq_edge_signature",
        ),
        sa.Index("ix_edge_src_lookup", "src_entity_type", "src_id", "relation"),
        sa.Index("ix_edge_dst_lookup", "dst_entity_type", "dst_id", "relation"),
    )

    src_entity_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    src_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dst_entity_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    dst_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relation: Mapped[EdgeRelation] = mapped_column(EDGE_RELATION_ENUM, nullable=False)
    edge_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.actor.id", ondelete="SET NULL"),
        nullable=True,
    )
