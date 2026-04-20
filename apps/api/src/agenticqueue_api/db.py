"""Database metadata shared by Alembic and future ORM models."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(schema="agenticqueue", naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base for future AgenticQueue models."""

    metadata = metadata


# Import ORM models so metadata is populated before Alembic autogenerate runs.
from agenticqueue_api import models as _models  # noqa: E402,F401
from agenticqueue_api import audit as _audit  # noqa: E402,F401
