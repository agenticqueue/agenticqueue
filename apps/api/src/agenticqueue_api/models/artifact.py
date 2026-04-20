"""Artifact entity models."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from pydantic import Field, field_validator
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import (
    IdentifiedTable,
    TimestampedSchema,
    TimestampedTable,
    jsonb_dict_column,
)
from agenticqueue_api.pgvector import embedding_column, normalize_embedding
from agenticqueue_api.search import (
    SEARCH_DOCUMENT_COLUMN_NAME,
    search_document_expression,
    search_document_index_name,
    search_text_expression,
    search_text_trgm_index_name,
    search_trigram_column_name,
)


class ArtifactModel(TimestampedSchema):
    """Pydantic schema for an artifact."""

    task_id: uuid.UUID
    run_id: uuid.UUID | None = None
    kind: str
    uri: str
    details: dict[str, Any] = Field(default_factory=dict)
    embedding: list[float] | None = None

    @field_validator("embedding", mode="before")
    @classmethod
    def _normalize_embedding(cls, value: object) -> list[float] | None:
        return normalize_embedding(value)


class ArtifactRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for an artifact row."""

    __tablename__ = "artifact"
    __table_args__ = (
        sa.Index(
            search_document_index_name(__tablename__),
            SEARCH_DOCUMENT_COLUMN_NAME,
            postgresql_using="gin",
        ),
        sa.Index(
            search_text_trgm_index_name(__tablename__),
            search_trigram_column_name(__tablename__),
            postgresql_using="gin",
            postgresql_ops={
                search_trigram_column_name(__tablename__): "gin_trgm_ops"
            },
        ),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.task.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.run.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    uri: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    details: Mapped[dict[str, Any]] = jsonb_dict_column()
    search_text: Mapped[str] = mapped_column(
        sa.Text(),
        sa.Computed(search_text_expression(__tablename__), persisted=True),
        nullable=False,
    )
    search_document: Mapped[str] = mapped_column(
        TSVECTOR(),
        sa.Computed(search_document_expression(__tablename__), persisted=True),
        nullable=False,
    )
    embedding: Mapped[list[float] | None] = embedding_column()
