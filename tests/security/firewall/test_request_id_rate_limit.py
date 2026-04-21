from __future__ import annotations

import uuid
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from agenticqueue_api.middleware.rate_limit import (
    ActorRateLimitMiddleware,
    ActorTokenBucket,
    _requires_rate_limit,
)
from agenticqueue_api.middleware.request_id import (
    REQUEST_ID_HEADER,
    TRACE_ID_HEADER,
    RequestIdMiddleware,
)


class StubAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, actor_id: uuid.UUID | None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._actor_id = actor_id

    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        if self._actor_id is not None:
            request.state.actor = SimpleNamespace(id=self._actor_id)
        return await call_next(request)


def _build_request_id_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/v1/echo")
    async def echo(request: Request) -> dict[str, str]:
        return {"request_id": request.state.request_id}

    return app


def _build_rate_limit_app(*, actor_id: uuid.UUID | None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ActorRateLimitMiddleware, rate_per_second=1.0, burst_size=1)
    app.add_middleware(StubAuthMiddleware, actor_id=actor_id)

    @app.get("/v1/tasks")
    async def tasks() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_request_id_middleware_prefers_request_headers_and_generates_fallback() -> None:
    with TestClient(_build_request_id_app()) as client:
        explicit = client.get("/v1/echo", headers={REQUEST_ID_HEADER: " req-123 "})
        assert explicit.status_code == 200
        assert explicit.json()["request_id"] == "req-123"
        assert explicit.headers[REQUEST_ID_HEADER] == "req-123"

        traced = client.get("/v1/echo", headers={TRACE_ID_HEADER: " trace-456 "})
        assert traced.status_code == 200
        assert traced.json()["request_id"] == "trace-456"
        assert traced.headers[REQUEST_ID_HEADER] == "trace-456"

        generated = client.get("/v1/echo")
        assert generated.status_code == 200
        uuid.UUID(generated.json()["request_id"])
        assert generated.headers[REQUEST_ID_HEADER] == generated.json()["request_id"]


def test_actor_token_bucket_refills_and_handles_zero_rate_retry_after() -> None:
    actor_id = uuid.uuid4()
    bucket = ActorTokenBucket(rate_per_second=2.0, burst_size=2)
    assert bucket.allow(actor_id, now=0.0) == (True, 0.0)
    assert bucket.allow(actor_id, now=0.0) == (True, 0.0)

    allowed, retry_after = bucket.allow(actor_id, now=0.0)
    assert not allowed
    assert retry_after == 0.5

    assert bucket.allow(actor_id, now=0.5) == (True, 0.0)

    zero_rate_bucket = ActorTokenBucket(rate_per_second=0.0, burst_size=0)
    allowed, retry_after = zero_rate_bucket.allow(uuid.uuid4(), now=0.0)
    assert not allowed
    assert retry_after == 1.0


def test_requires_rate_limit_matches_agenticqueue_routes() -> None:
    assert _requires_rate_limit("/v1/tasks")
    assert _requires_rate_limit("/task-types")
    assert _requires_rate_limit("/openapi.json")
    assert not _requires_rate_limit("/tests/graph-timeout")
    assert not _requires_rate_limit("/docs")
    assert not _requires_rate_limit("/healthz")


def test_rate_limit_middleware_bypasses_actorless_requests_and_limits_actor() -> None:
    with TestClient(_build_rate_limit_app(actor_id=None)) as client:
        first = client.get("/v1/tasks")
        second = client.get("/v1/tasks")
        assert first.status_code == 200
        assert second.status_code == 200

    actor_id = uuid.uuid4()
    with TestClient(_build_rate_limit_app(actor_id=actor_id)) as client:
        healthz = client.get("/healthz")
        allowed = client.get("/v1/tasks")
        limited = client.get("/v1/tasks")

    assert healthz.status_code == 200
    assert allowed.status_code == 200
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "1"
    assert limited.json()["message"] == "Rate limit exceeded"
    assert limited.json()["details"] == {
        "actor_id": str(actor_id),
        "rate_limit_rps": 1.0,
        "burst_size": 1,
    }
