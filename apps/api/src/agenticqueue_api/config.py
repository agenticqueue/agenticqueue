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
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PACKET_SCOPE_MAX_FILES = 200
DEFAULT_PACKET_CACHE_MAX_ENTRIES = 200
DEFAULT_PACKET_CACHE_TTL_SECONDS = 300
DEFAULT_PACKET_PREFETCH_WIDTH = 2
DEFAULT_MAX_BODY_BYTES = 1024 * 1024
DEFAULT_RATE_LIMIT_RPS = 100
DEFAULT_RATE_LIMIT_BURST = 500
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


def get_direct_sync_database_url() -> str:
    """Return a direct Postgres URL suitable for LISTEN/NOTIFY."""

    parts = urlsplit(get_sync_database_url())
    hostname = parts.hostname
    port = parts.port
    direct_hostname = "db" if hostname == "pgbouncer" else hostname
    direct_port = 5432 if port == 6432 else 54329 if port == 64329 else port

    netloc = ""
    if parts.username:
        netloc += parts.username
        if parts.password:
            netloc += f":{parts.password}"
        netloc += "@"
    if direct_hostname:
        netloc += direct_hostname
    if direct_port is not None:
        netloc += f":{direct_port}"
    return urlunsplit(parts._replace(netloc=netloc))


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


def get_max_body_bytes() -> int:
    """Return the default HTTP request body cap in bytes."""

    return int(
        os.getenv("AGENTICQUEUE_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES)
    )


def get_rate_limit_rps() -> int:
    """Return the per-actor sustained request budget."""

    return int(
        os.getenv("AGENTICQUEUE_RATE_LIMIT_RPS", DEFAULT_RATE_LIMIT_RPS)
    )


def get_rate_limit_burst() -> int:
    """Return the per-actor burst request budget."""

    return int(
        os.getenv("AGENTICQUEUE_RATE_LIMIT_BURST", DEFAULT_RATE_LIMIT_BURST)
    )


def get_policies_dir() -> Path:
    """Return the policy-pack directory."""

    configured = os.getenv("AGENTICQUEUE_POLICIES_DIR") or os.getenv("POLICIES_DIR")
    if configured:
        return Path(configured)
    return DEFAULT_POLICIES_DIR


def get_repo_root() -> Path:
    """Return the local checkout root used for repo-relative scope resolution."""

    configured = os.getenv("AGENTICQUEUE_REPO_ROOT") or os.getenv("REPO_ROOT")
    if configured:
        return Path(configured)
    return DEFAULT_REPO_ROOT


def get_packet_scope_max_files() -> int:
    """Return the max number of files a resolved packet scope may include."""

    return int(
        os.getenv(
            "AGENTICQUEUE_PACKET_SCOPE_MAX_FILES",
            DEFAULT_PACKET_SCOPE_MAX_FILES,
        )
    )


def get_packet_cache_max_entries() -> int:
    """Return the packet cache LRU capacity per worker."""

    return int(
        os.getenv(
            "AGENTICQUEUE_PACKET_CACHE_MAX_ENTRIES",
            DEFAULT_PACKET_CACHE_MAX_ENTRIES,
        )
    )


def get_packet_cache_ttl_seconds() -> int:
    """Return the packet cache TTL in seconds."""

    return int(
        os.getenv(
            "AGENTICQUEUE_PACKET_CACHE_TTL_SECONDS",
            DEFAULT_PACKET_CACHE_TTL_SECONDS,
        )
    )


def get_packet_prefetch_width() -> int:
    """Return the number of speculative packet prefetch slots."""

    return int(
        os.getenv(
            "AGENTICQUEUE_PACKET_PREFETCH_WIDTH",
            DEFAULT_PACKET_PREFETCH_WIDTH,
        )
    )


def get_reload_enabled() -> bool:
    """Return whether dev-time task type reloads are enabled."""

    configured = os.getenv("AGENTICQUEUE_RELOAD") or os.getenv("RELOAD") or ""
    return configured.strip().lower() in TRUE_ENV_VALUES
