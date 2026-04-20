"""Memory-layer storage models."""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
import uuid
from typing import Final

import sqlalchemy as sa
from pydantic import Field, field_validator
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import CreatedSchema, CreatedTable, IdentifiedTable
from agenticqueue_api.pgvector import embedding_column, normalize_embedding


class MemoryLayer(StrEnum):
    """Storage tiers used by the retrieval system."""

    CANONICAL = "canonical"
    PROJECT = "project"
    EPISODIC = "episodic"
    POLICY = "policy"
    USER = "user"


MEMORY_LAYER_ENUM = sa.Enum(
    MemoryLayer,
    name="memory_layer",
    native_enum=False,
    validate_strings=True,
    values_callable=lambda members: [member.value for member in members],
)

MEMORY_LAYER_SCOPE_HINTS: Final[dict[MemoryLayer, str]] = {
    MemoryLayer.CANONICAL: "workspace- or corpus-level canonical source id",
    MemoryLayer.PROJECT: "project.id for project-scoped memory",
    MemoryLayer.EPISODIC: "run.id or task.id for short-lived execution memory",
    MemoryLayer.POLICY: "policy.id for policy-pack memory",
    MemoryLayer.USER: "actor.id for agent or human user memory",
}


class MemoryItemModel(CreatedSchema):
    """Pydantic schema for one stored memory item."""

    layer: MemoryLayer
    scope_id: uuid.UUID
    content_text: str
    content_hash: str
    embedding: list[float] | None = None
    source_ref: str | None = None
    surface_area: list[str] = Field(default_factory=list)
    last_accessed_at: dt.datetime | None = None
    access_count: int = 0

    @field_validator("content_text")
    @classmethod
    def _validate_content_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("content_text must not be empty")
        return normalized

    @field_validator("content_hash")
    @classmethod
    def _validate_content_hash(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("content_hash must not be empty")
        return normalized

    @field_validator("source_ref")
    @classmethod
    def _normalize_source_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("surface_area", mode="before")
    @classmethod
    def _normalize_surface_area(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, (list, tuple)):
            raise ValueError("surface_area must be a list of strings")
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("surface_area items must be strings")
            cleaned = item.strip()
            if cleaned:
                normalized.append(cleaned)
        return normalized

    @field_validator("access_count")
    @classmethod
    def _validate_access_count(cls, value: int) -> int:
        if value < 0:
            raise ValueError("access_count must be non-negative")
        return value

    @field_validator("embedding", mode="before")
    @classmethod
    def _normalize_embedding(cls, value: object) -> list[float] | None:
        return normalize_embedding(value)

    @property
    def scope_hint(self) -> str:
        """Return the layer-specific meaning of scope_id."""

        return MEMORY_LAYER_SCOPE_HINTS[self.layer]


class MemoryItemRecord(IdentifiedTable, CreatedTable, Base):
    """SQLAlchemy model for one memory row."""

    __tablename__ = "memory_item"
    __table_args__ = (
        sa.UniqueConstraint(
            "layer",
            "scope_id",
            "content_hash",
            name="uq_memory_item_layer_scope_id_content_hash",
        ),
        sa.Index(
            "ix_memory_item_surface_area_gin",
            "surface_area",
            postgresql_using="gin",
        ),
    )

    layer: Mapped[MemoryLayer] = mapped_column(MEMORY_LAYER_ENUM, nullable=False)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    content_text: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    content_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    embedding: Mapped[list[float] | None] = embedding_column()
    source_ref: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    surface_area: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(sa.Text()),
        nullable=False,
        default=list,
        server_default=sa.text("ARRAY[]::text[]"),
    )
    last_accessed_at: Mapped[dt.datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    access_count: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        default=0,
        server_default=sa.text("0"),
    )
