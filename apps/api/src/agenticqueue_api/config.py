"""Shared configuration helpers for AgenticQueue API tooling."""

from __future__ import annotations

import os

DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://agenticqueue:agenticqueue@127.0.0.1:54329/agenticqueue"
)
ASYNC_PREFIX = "postgresql+asyncpg://"
SQLALCHEMY_SYNC_PREFIX = "postgresql+psycopg://"
PSYCOPG_PREFIX = "postgresql://"


def get_database_url() -> str:
    """Return the async database URL used by Alembic and the API."""
    return (
        os.getenv("AGENTICQUEUE_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )


def get_sync_database_url() -> str:
    """Return a psycopg URL derived from the configured async database URL."""
    url = get_database_url()
    if url.startswith(ASYNC_PREFIX):
        return PSYCOPG_PREFIX + url[len(ASYNC_PREFIX) :]
    if url.startswith(SQLALCHEMY_SYNC_PREFIX):
        return PSYCOPG_PREFIX + url[len(SQLALCHEMY_SYNC_PREFIX) :]
    if url.startswith("postgresql://"):
        return url
    return url
