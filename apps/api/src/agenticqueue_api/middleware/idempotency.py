"""Idempotency middleware and persistence helpers."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

import sqlalchemy as sa
from fastapi.responses import JSONResponse
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from agenticqueue_api.errors import error_payload

if TYPE_CHECKING:
    from agenticqueue_api.models.idempotency_key import IdempotencyKeyRecord

IDEMPOTENCY_KEY_HEADER: Final = "Idempotency-Key"
IDEMPOTENCY_CONFLICT_HEADER: Final = "X-Idempotency-Conflict"
IDEMPOTENCY_REPLAYED_HEADER: Final = "X-Idempotency-Replayed"
IDEMPOTENCY_TTL: Final = dt.timedelta(hours=24)
_ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$", re.IGNORECASE)
_MUTATING_METHODS = frozenset({"POST", "PATCH", "DELETE"})


@dataclass(frozen=True)
class IdempotencyStats:
    """Aggregated cache statistics for CLI inspection."""

    hit_count: int
    row_count: int
    expired_count: int
    active_count: int


@dataclass(frozen=True)
class IdempotencyLookupResult:
    """Lookup result for one idempotency key."""

    kind: str
    record: IdempotencyKeyRecord | None = None


def _record_type() -> type["IdempotencyKeyRecord"]:
    from agenticqueue_api.models.idempotency_key import IdempotencyKeyRecord

    return IdempotencyKeyRecord


def normalize_idempotency_key(raw_key: str) -> str:
    """Validate and normalize one idempotency key."""

    key = raw_key.strip()
    if not key:
        raise ValueError("Idempotency-Key header is required")

    try:
        return str(uuid.UUID(key))
    except ValueError:
        if _ULID_PATTERN.fullmatch(key):
            return key.upper()

    raise ValueError("Idempotency-Key must be a UUID or ULID")


def hash_request_body(body: bytes) -> bytes:
    """Return the raw SHA-256 digest for one request body."""

    return hashlib.sha256(body).digest()


def requires_idempotency(request: Request) -> bool:
    """Return whether this request must supply an idempotency key."""

    if request.method not in _MUTATING_METHODS:
        return False

    path = request.url.path
    if path in {"/v1/auth/login", "/v1/auth/logout"}:
        return False
    if request.method == "DELETE" and path.startswith("/v1/auth/tokens/"):
        return False
    return (
        path == "/setup"
        or path == "/task-types"
        or path.startswith("/v1/")
        or path.startswith("/tasks/")
        or path.startswith("/learnings/drafts/")
    )


def lookup_idempotency_key(
    session: Session,
    *,
    key: str,
    actor_id: uuid.UUID,
    body_sha256: bytes,
    now: dt.datetime | None = None,
) -> IdempotencyLookupResult:
    """Resolve an idempotency key to a cache miss, hit, or conflict."""

    current_time = now or dt.datetime.now(dt.UTC)
    record_type = _record_type()
    record = session.get(record_type, key)
    if record is None:
        return IdempotencyLookupResult(kind="miss")

    if record.expires_at <= current_time:
        session.delete(record)
        session.flush()
        return IdempotencyLookupResult(kind="miss")

    if record.actor_id != actor_id or record.body_sha256 != body_sha256:
        return IdempotencyLookupResult(kind="conflict", record=record)

    record.replay_count += 1
    session.flush()
    return IdempotencyLookupResult(kind="hit", record=record)


def store_idempotency_response(
    session: Session,
    *,
    key: str,
    actor_id: uuid.UUID,
    body_sha256: bytes,
    response_status: int,
    response_body: str,
    now: dt.datetime | None = None,
) -> IdempotencyKeyRecord:
    """Persist one successful mutating response for replay."""

    current_time = now or dt.datetime.now(dt.UTC)
    record_type = _record_type()
    record = record_type(
        key=key,
        actor_id=actor_id,
        body_sha256=body_sha256,
        response_status=response_status,
        response_body=response_body,
        replay_count=0,
        expires_at=current_time + IDEMPOTENCY_TTL,
    )
    session.add(record)
    session.flush()
    return record


def cleanup_expired_idempotency_keys(
    session: Session,
    *,
    now: dt.datetime | None = None,
) -> int:
    """Delete expired idempotency rows and return the delete count."""

    current_time = now or dt.datetime.now(dt.UTC)
    record_type = _record_type()
    result = session.execute(
        sa.delete(record_type).where(record_type.expires_at <= current_time)
    )
    return int(cast(CursorResult[Any], result).rowcount or 0)


def get_idempotency_stats(
    session: Session,
    *,
    now: dt.datetime | None = None,
) -> IdempotencyStats:
    """Return aggregate cache statistics."""

    current_time = now or dt.datetime.now(dt.UTC)
    record_type = _record_type()
    hit_count = int(
        session.scalar(
            sa.select(sa.func.coalesce(sa.func.sum(record_type.replay_count), 0))
        )
        or 0
    )
    row_count = int(
        session.scalar(sa.select(sa.func.count()).select_from(record_type)) or 0
    )
    expired_count = int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(record_type)
            .where(record_type.expires_at <= current_time)
        )
        or 0
    )
    active_count = int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(record_type)
            .where(record_type.expires_at > current_time)
        )
        or 0
    )
    return IdempotencyStats(
        hit_count=hit_count,
        row_count=row_count,
        expired_count=expired_count,
        active_count=active_count,
    )


async def _consume_response_body(response: Response) -> bytes:
    """Read the response body from the response iterator."""

    stream = cast(
        AsyncIterator[bytes | str | memoryview],
        getattr(response, "body_iterator"),
    )
    chunks = [
        chunk.encode("utf-8") if isinstance(chunk, str) else bytes(chunk)
        async for chunk in stream
    ]
    return b"".join(chunks)


def _clone_response(response: Response, body: bytes) -> Response:
    """Clone a response after its body iterator has been consumed."""

    headers = dict(response.headers)
    return Response(
        content=body,
        status_code=response.status_code,
        headers=headers,
        media_type=response.media_type,
        background=response.background,
    )


def _cached_response(record: "IdempotencyKeyRecord") -> Response:
    """Render a cached idempotent replay response."""

    headers = {IDEMPOTENCY_REPLAYED_HEADER: "true"}
    return Response(
        content=record.response_body,
        status_code=record.response_status,
        media_type="application/json",
        headers=headers,
    )


def _conflict_response() -> JSONResponse:
    """Render a structured conflict response for mismatched replays."""

    return JSONResponse(
        status_code=409,
        content=error_payload(
            status_code=409,
            message="Idempotency-Key was already used for a different request body",
        ),
        headers={IDEMPOTENCY_CONFLICT_HEADER: "true"},
    )


class IdempotencyKeyMiddleware(BaseHTTPMiddleware):
    """Replay-safe middleware for mutating AgenticQueue endpoints."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if not requires_idempotency(request):
            return await call_next(request)

        raw_key = request.headers.get(IDEMPOTENCY_KEY_HEADER)
        if raw_key is None:
            return JSONResponse(
                status_code=400,
                content=error_payload(
                    status_code=400,
                    message="Idempotency-Key header is required",
                ),
            )

        try:
            key = normalize_idempotency_key(raw_key)
        except ValueError as error:
            return JSONResponse(
                status_code=400,
                content=error_payload(status_code=400, message=str(error)),
            )

        actor = getattr(request.state, "actor", None)
        if actor is None:
            return await call_next(request)

        body = await request.body()
        body_sha256 = hash_request_body(body)

        with request.app.state.session_factory() as session:
            lookup = lookup_idempotency_key(
                session,
                key=key,
                actor_id=actor.id,
                body_sha256=body_sha256,
            )
            session.commit()

        if lookup.kind == "hit" and lookup.record is not None:
            return _cached_response(lookup.record)
        if lookup.kind == "conflict":
            return _conflict_response()

        response = await call_next(request)
        response_body = await _consume_response_body(response)
        replayable_response = _clone_response(response, response_body)

        if replayable_response.status_code >= 400:
            return replayable_response

        with request.app.state.session_factory() as session:
            store_idempotency_response(
                session,
                key=key,
                actor_id=actor.id,
                body_sha256=body_sha256,
                response_status=replayable_response.status_code,
                response_body=response_body.decode("utf-8"),
            )
            session.commit()

        return replayable_response


def stats_as_json(stats: IdempotencyStats) -> str:
    """Render stats in the CLI's JSON shape."""

    return json.dumps(
        {
            "active_count": stats.active_count,
            "expired_count": stats.expired_count,
            "hit_count": stats.hit_count,
            "row_count": stats.row_count,
        },
        sort_keys=True,
    )
