"""Database metadata shared by Alembic and future ORM models."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
import hashlib
import logging
import time

import sqlalchemy as sa
from sqlalchemy import MetaData
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import DeclarativeBase, Session

from agenticqueue_api.config import (
    get_graph_traversal_timeout_ms,
    get_role_statement_timeout_ms,
    get_write_statement_timeout_ms,
)

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(schema="agenticqueue", naming_convention=NAMING_CONVENTION)
logger = logging.getLogger(__name__)
STATEMENT_TIMEOUT_SQLSTATE = "57014"


class Base(DeclarativeBase):
    """Declarative base for future AgenticQueue models."""

    metadata = metadata


class StatementTimeoutError(RuntimeError):
    """Raised when Postgres cancels a statement due to statement_timeout."""

    def __init__(
        self,
        *,
        endpoint: str,
        sql_fingerprint: str | None,
        elapsed_ms: int,
        timeout_ms: int,
    ) -> None:
        self.endpoint = endpoint
        self.sql_fingerprint = sql_fingerprint
        self.elapsed_ms = elapsed_ms
        self.timeout_ms = timeout_ms
        super().__init__(
            f"statement timeout in {endpoint} after {elapsed_ms}ms (budget {timeout_ms}ms)"
        )


def _is_statement_timeout(error: BaseException) -> bool:
    sqlstate = getattr(error, "sqlstate", None)
    message = str(error).lower()
    return sqlstate == STATEMENT_TIMEOUT_SQLSTATE and "statement timeout" in message


def _statement_fingerprint(statement: str | None) -> str | None:
    if not statement:
        return None
    normalized = " ".join(statement.split()).lower()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _set_local_statement_timeout(session: Session, milliseconds: int) -> None:
    session.execute(
        sa.text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": f"{milliseconds}ms"},
    )


@contextmanager
def timeout_ms(
    session: Session,
    milliseconds: int,
    *,
    endpoint: str,
) -> Iterator[None]:
    """Apply a transaction-local statement timeout for one request block."""

    if milliseconds < 1:
        raise ValueError("milliseconds must be positive")

    _set_local_statement_timeout(session, milliseconds)
    started = time.perf_counter()
    try:
        yield
    except DBAPIError as error:
        original = error.orig if error.orig is not None else error
        if not _is_statement_timeout(original):
            raise
        elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
        sql_fingerprint = _statement_fingerprint(error.statement)
        logger.warning(
            "statement-timeout endpoint=%s sql_fingerprint=%s elapsed_ms=%s timeout_ms=%s",
            endpoint,
            sql_fingerprint or "unknown",
            elapsed_ms,
            milliseconds,
        )
        raise StatementTimeoutError(
            endpoint=endpoint,
            sql_fingerprint=sql_fingerprint,
            elapsed_ms=elapsed_ms,
            timeout_ms=milliseconds,
        ) from error


def graph_timeout(
    session: Session,
    *,
    endpoint: str,
) -> AbstractContextManager[None]:
    """Return the standard graph-read timeout context manager."""

    return timeout_ms(
        session,
        get_graph_traversal_timeout_ms(),
        endpoint=endpoint,
    )


def write_timeout(
    session: Session,
    *,
    endpoint: str,
) -> AbstractContextManager[None]:
    """Return the standard write-request timeout context manager."""

    return timeout_ms(
        session,
        get_write_statement_timeout_ms(),
        endpoint=endpoint,
    )


def role_statement_timeout_ms() -> int:
    """Return the persisted role-level statement timeout budget."""

    return get_role_statement_timeout_ms()


# Import ORM models so metadata is populated before Alembic autogenerate runs.
from agenticqueue_api import models as _models  # noqa: E402,F401
from agenticqueue_api import audit as _audit  # noqa: E402,F401
