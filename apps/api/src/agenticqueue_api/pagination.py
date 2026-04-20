"""Cursor pagination helpers for REST list endpoints."""

from __future__ import annotations

import base64
import datetime as dt
import json
import uuid
from enum import Enum
from typing import Any

import sqlalchemy as sa
from fastapi.encoders import jsonable_encoder
from fastapi import status

from agenticqueue_api.errors import raise_api_error

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
NEXT_CURSOR_HEADER = "X-Next-Cursor"
LIMIT_HEADER = "X-List-Limit"
RESERVED_QUERY_PARAMS = frozenset({"cursor", "limit"})


def encode_cursor(values: list[Any]) -> str:
    """Encode a cursor payload as an opaque URL-safe token."""

    payload = json.dumps(jsonable_encoder(values), separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, *, expected_size: int) -> list[Any]:
    """Decode one opaque cursor token."""

    token = cursor.strip()
    if not token:
        raise_api_error(status.HTTP_400_BAD_REQUEST, "Cursor token is empty")
    padding = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(f"{token}{padding}".encode("ascii"))
        values = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise_api_error(status.HTTP_400_BAD_REQUEST, "Cursor token is invalid")
    if not isinstance(values, list) or len(values) != expected_size:
        raise_api_error(status.HTTP_400_BAD_REQUEST, "Cursor token is invalid")
    return values


def coerce_cursor_value(raw_value: Any, python_type: type[Any]) -> Any:
    """Convert a decoded cursor value into the expected Python type."""

    if raw_value is None:
        return None
    if python_type is uuid.UUID:
        try:
            return uuid.UUID(str(raw_value))
        except (TypeError, ValueError):
            raise_api_error(status.HTTP_400_BAD_REQUEST, "Cursor token is invalid")
    if python_type is dt.datetime:
        try:
            return dt.datetime.fromisoformat(str(raw_value))
        except ValueError:
            raise_api_error(status.HTTP_400_BAD_REQUEST, "Cursor token is invalid")
    if python_type is dt.date:
        try:
            return dt.date.fromisoformat(str(raw_value))
        except ValueError:
            raise_api_error(status.HTTP_400_BAD_REQUEST, "Cursor token is invalid")
    if python_type in {int, float, str, bool}:
        try:
            return python_type(raw_value)
        except (TypeError, ValueError):
            raise_api_error(status.HTTP_400_BAD_REQUEST, "Cursor token is invalid")
    if issubclass(python_type, Enum):
        try:
            return python_type(raw_value)
        except ValueError:
            raise_api_error(status.HTTP_400_BAD_REQUEST, "Cursor token is invalid")
    return raw_value


def apply_cursor_clause(
    statement: Any,
    *,
    columns: list[Any],
    cursor_values: list[Any] | None,
) -> Any:
    """Apply a lexicographic cursor filter to an ordered SQL statement."""

    if cursor_values is None:
        return statement
    conditions = []
    for index, column in enumerate(columns):
        comparisons = [
            columns[offset] == cursor_values[offset] for offset in range(index)
        ]
        comparisons.append(column > cursor_values[index])
        conditions.append(sa.and_(*comparisons))
    return statement.where(sa.or_(*conditions))

