"""Packet version entity models."""

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


class PacketVersionModel(CreatedSchema):
    """Pydantic schema for a packet snapshot."""

    task_id: uuid.UUID
    packet_hash: str
    payload: dict[str, Any] = Field(default_factory=dict)


class PacketVersionRecord(IdentifiedTable, CreatedTable, Base):
    """SQLAlchemy model for a packet version row."""

    __tablename__ = "packet_version"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.task.id", ondelete="CASCADE"),
        nullable=False,
    )
    packet_hash: Mapped[str] = mapped_column(
        sa.String(128),
        nullable=False,
        unique=True,
    )
    payload: Mapped[dict[str, Any]] = jsonb_dict_column()
