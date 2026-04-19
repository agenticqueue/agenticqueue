"""Shared helpers for AgenticQueue entity models."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


class SchemaModel(BaseModel):
    """Base Pydantic schema for AgenticQueue entities."""

    model_config = ConfigDict(from_attributes=True)


class CreatedSchema(SchemaModel):
    """Schema fields shared by immutable records."""

    id: uuid.UUID
    created_at: dt.datetime


class TimestampedSchema(CreatedSchema):
    """Schema fields shared by mutable records."""

    updated_at: dt.datetime


class IdentifiedTable:
    """Mixin that provides a UUID primary key."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    )


class CreatedTable:
    """Mixin that provides a creation timestamp."""

    created_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class TimestampedTable(CreatedTable):
    """Mixin that provides creation and update timestamps."""

    updated_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )


def jsonb_dict_column() -> Any:
    """Return a JSONB dict column with a Python-side default."""

    return mapped_column(JSONB, nullable=False, default=dict)


def jsonb_list_column() -> Any:
    """Return a JSONB list column with a Python-side default."""

    return mapped_column(JSONB, nullable=False, default=list)
