"""Postgres-backed login rate-limit models."""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.models.shared import IdentifiedTable, TimestampedTable


class AuthRateLimitRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy model for one IP login-attempt window."""

    __tablename__ = "auth_rate_limit"
    __table_args__ = (
        sa.UniqueConstraint(
            "ip",
            "window_start_minute",
            name="uq_auth_rate_limit_ip_window_start_minute",
        ),
    )

    ip: Mapped[str] = mapped_column(sa.String(45), nullable=False, index=True)
    window_start_minute: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    count: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
