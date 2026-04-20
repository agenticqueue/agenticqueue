"""Shared configuration helpers for AgenticQueue API tooling."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://agenticqueue:agenticqueue@127.0.0.1:64329/agenticqueue"
)
DEFAULT_TOKEN_SIGNING_SECRET = "agenticqueue-dev-token-signing-secret"
DEFAULT_EMBEDDING_DIMENSION = 768
DEFAULT_VECTOR_IVFFLAT_LISTS = 100
DEFAULT_ROLE_STATEMENT_TIMEOUT_MS = 5000
DEFAULT_GRAPH_TRAVERSAL_TIMEOUT_MS = 2000
DEFAULT_WRITE_STATEMENT_TIMEOUT_MS = 10000
DEFAULT_TASK_TYPES_DIR = Path(__file__).resolve().parents[4] / "task_types"
DEFAULT_POLICIES_DIR = Path(__file__).resolve().parents[4] / "policies"
ASYNC_PREFIX = "postgresql+asyncpg://"
SQLALCHEMY_SYNC_PREFIX = "postgresql+psycopg://"
PSYCOPG_PREFIX = "postgresql://"
TRUE_ENV_VALUES = {"1", "true", "yes", "on"}


def _with_query_defaults(url: str, defaults: dict[str, str]) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key, value in defaults.items():
        query.setdefault(key, value)
    return urlunsplit(parts._replace(query=urlencode(query)))


def _drop_query_keys(url: str, keys: set[str]) -> str:
    parts = urlsplit(url)
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in keys
    ]
    return urlunsplit(parts._replace(query=urlencode(query_items)))


def get_database_url() -> str:
    """Return the async database URL used by Alembic and the API."""
    url = (
        os.getenv("AGENTICQUEUE_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )
    if url.startswith(ASYNC_PREFIX):
        return _with_query_defaults(url, {"prepared_statement_cache_size": "0"})
    return url


def get_sync_database_url() -> str:
    """Return a psycopg URL derived from the configured async database URL."""
    url = _drop_query_keys(get_database_url(), {"prepared_statement_cache_size"})
    if url.startswith(ASYNC_PREFIX):
        return PSYCOPG_PREFIX + url[len(ASYNC_PREFIX) :]
    if url.startswith(SQLALCHEMY_SYNC_PREFIX):
        return PSYCOPG_PREFIX + url[len(SQLALCHEMY_SYNC_PREFIX) :]
    if url.startswith("postgresql://"):
        return url
    return url


def get_sqlalchemy_sync_database_url() -> str:
    """Return a sync SQLAlchemy URL derived from the configured async database URL."""
    url = _drop_query_keys(get_database_url(), {"prepared_statement_cache_size"})
    if url.startswith(ASYNC_PREFIX):
        return SQLALCHEMY_SYNC_PREFIX + url[len(ASYNC_PREFIX) :]
    if url.startswith(PSYCOPG_PREFIX):
        return SQLALCHEMY_SYNC_PREFIX + url[len(PSYCOPG_PREFIX) :]
    if url.startswith(SQLALCHEMY_SYNC_PREFIX):
        return url
    return url


def get_psycopg_connect_args() -> dict[str, int | None]:
    """Disable prepared statements for transaction-pooled PgBouncer connections."""

    return {"prepare_threshold": None}


def get_token_signing_secret() -> str:
    """Return the HMAC secret used for API token hashing."""
    return (
        os.getenv("AGENTICQUEUE_TOKEN_SIGNING_SECRET")
        or os.getenv("TOKEN_SIGNING_SECRET")
        or DEFAULT_TOKEN_SIGNING_SECRET
    )


def get_embedding_dimension() -> int:
    """Return the configured pgvector embedding dimension."""
    return int(
        os.getenv("AGENTICQUEUE_EMBEDDING_DIMENSION", DEFAULT_EMBEDDING_DIMENSION)
    )


def get_vector_ivfflat_lists() -> int:
    """Return the ivfflat list count used for embedding indexes."""
    return int(
        os.getenv("AGENTICQUEUE_VECTOR_IVFFLAT_LISTS", DEFAULT_VECTOR_IVFFLAT_LISTS)
    )


def get_role_statement_timeout_ms() -> int:
    """Return the default role-level statement timeout in milliseconds."""

    return int(
        os.getenv(
            "AGENTICQUEUE_ROLE_STATEMENT_TIMEOUT_MS",
            DEFAULT_ROLE_STATEMENT_TIMEOUT_MS,
        )
    )


def get_graph_traversal_timeout_ms() -> int:
    """Return the graph-read timeout budget in milliseconds."""

    return int(
        os.getenv(
            "AGENTICQUEUE_GRAPH_TRAVERSAL_TIMEOUT_MS",
            DEFAULT_GRAPH_TRAVERSAL_TIMEOUT_MS,
        )
    )


def get_write_statement_timeout_ms() -> int:
    """Return the mutating-request timeout budget in milliseconds."""

    return int(
        os.getenv(
            "AGENTICQUEUE_WRITE_STATEMENT_TIMEOUT_MS",
            DEFAULT_WRITE_STATEMENT_TIMEOUT_MS,
        )
    )


def get_task_types_dir() -> Path:
    """Return the task type registry directory."""

    configured = os.getenv("AGENTICQUEUE_TASK_TYPES_DIR") or os.getenv("TASK_TYPES_DIR")
    if configured:
        return Path(configured)
    return DEFAULT_TASK_TYPES_DIR


def get_policies_dir() -> Path:
    """Return the policy-pack directory."""

    configured = os.getenv("AGENTICQUEUE_POLICIES_DIR") or os.getenv("POLICIES_DIR")
    if configured:
        return Path(configured)
    return DEFAULT_POLICIES_DIR


def get_reload_enabled() -> bool:
    """Return whether dev-time task type reloads are enabled."""

    configured = os.getenv("AGENTICQUEUE_RELOAD") or os.getenv("RELOAD") or ""
    return configured.strip().lower() in TRUE_ENV_VALUES
