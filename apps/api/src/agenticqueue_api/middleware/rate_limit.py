"""Per-actor rate limiting middleware for the REST surface."""

from __future__ import annotations

import math
import threading
import time
import uuid
from dataclasses import dataclass

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from agenticqueue_api.errors import error_payload


@dataclass
class _BucketState:
    tokens: float
    updated_at: float


class ActorTokenBucket:
    """Simple in-memory token bucket keyed by actor ID."""

    def __init__(
        self,
        *,
        rate_per_second: float,
        burst_size: int,
    ) -> None:
        self.rate_per_second = rate_per_second
        self.burst_size = float(burst_size)
        self._buckets: dict[uuid.UUID, _BucketState] = {}
        self._lock = threading.Lock()

    def allow(
        self,
        actor_id: uuid.UUID,
        *,
        now: float | None = None,
        tokens: float = 1.0,
    ) -> tuple[bool, float]:
        """Return whether one request is allowed and the retry delay when denied."""

        current_time = time.monotonic() if now is None else now
        with self._lock:
            state = self._buckets.get(actor_id)
            if state is None:
                state = _BucketState(tokens=self.burst_size, updated_at=current_time)
                self._buckets[actor_id] = state
            else:
                elapsed = max(0.0, current_time - state.updated_at)
                state.tokens = min(
                    self.burst_size,
                    state.tokens + (elapsed * self.rate_per_second),
                )
                state.updated_at = current_time

            if state.tokens >= tokens:
                state.tokens -= tokens
                return True, 0.0

            deficit = tokens - state.tokens
            retry_after = (
                deficit / self.rate_per_second if self.rate_per_second else 1.0
            )
            return False, retry_after


def _requires_rate_limit(path: str) -> bool:
    return path.startswith("/v1/") or path == "/task-types" or path == "/openapi.json"


class ActorRateLimitMiddleware(BaseHTTPMiddleware):
    """Apply token-bucket rate limiting to authenticated requests."""

    def __init__(
        self,
        app,
        *,
        rate_per_second: float,
        burst_size: int,
    ) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._limiter = ActorTokenBucket(
            rate_per_second=rate_per_second,
            burst_size=burst_size,
        )

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if not _requires_rate_limit(request.url.path):
            return await call_next(request)

        actor = getattr(request.state, "actor", None)
        actor_id = getattr(actor, "id", None)
        if not isinstance(actor_id, uuid.UUID):
            return await call_next(request)

        allowed, retry_after = self._limiter.allow(actor_id)
        if allowed:
            return await call_next(request)

        headers = {"Retry-After": str(max(1, math.ceil(retry_after)))}
        return JSONResponse(
            status_code=429,
            content=error_payload(
                status_code=429,
                message="Rate limit exceeded",
                details={
                    "actor_id": str(actor_id),
                    "rate_limit_rps": self._limiter.rate_per_second,
                    "burst_size": int(self._limiter.burst_size),
                },
            ),
            headers=headers,
        )
