from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from agenticqueue_api.middleware import idempotency as idempotency_module
from agenticqueue_api.middleware.idempotency import (
    IDEMPOTENCY_CONFLICT_HEADER,
    IDEMPOTENCY_KEY_HEADER,
    IDEMPOTENCY_REPLAYED_HEADER,
    IdempotencyKeyMiddleware,
    cleanup_expired_idempotency_keys,
    get_idempotency_stats,
    normalize_idempotency_key,
    requires_idempotency,
    stats_as_json,
)


class TempBase(DeclarativeBase):
    pass


class UTCDateTime(sa.TypeDecorator[dt.datetime]):
    impl = sa.String()
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect: sa.Dialect) -> str | None:
        del dialect
        if value is None:
            return None
        return value.astimezone(dt.UTC).isoformat()

    def process_result_value(
        self,
        value: str | None,
        dialect: sa.Dialect,
    ) -> dt.datetime | None:
        del dialect
        if value is None:
            return None
        return dt.datetime.fromisoformat(value)


class TempActorRecord(TempBase):
    __tablename__ = "actor"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)


class TempIdempotencyKeyRecord(TempBase):
    __tablename__ = "idempotency_key"

    key: Mapped[str] = mapped_column(sa.Text(), primary_key=True)
    actor_id: Mapped[str] = mapped_column(
        sa.String(36),
        sa.ForeignKey("actor.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    body_sha256: Mapped[bytes] = mapped_column(sa.LargeBinary(), nullable=False)
    response_status: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    response_body: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    replay_count: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        default=0,
        server_default="0",
    )
    expires_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)


@pytest.fixture(autouse=True)
def patch_record_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        idempotency_module,
        "_record_type",
        lambda: TempIdempotencyKeyRecord,
    )


@pytest.fixture(scope="session")
def engine() -> Iterator[sa.Engine]:
    engine = sa.create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TempBase.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(engine: sa.Engine) -> Iterator[None]:
    with engine.begin() as connection:
        connection.execute(sa.delete(TempIdempotencyKeyRecord))
        connection.execute(sa.delete(TempActorRecord))
    yield


@pytest.fixture
def session_factory(engine: sa.Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def seed_actor(
    session_factory: sessionmaker[Session],
    *,
    actor_id: str,
) -> None:
    with session_factory() as session:
        session.add(TempActorRecord(id=actor_id))
        session.commit()


class StubAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, actor_id: str) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._actor_id = actor_id

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.actor = SimpleNamespace(id=self._actor_id)
        return await call_next(request)


def build_echo_app(
    session_factory: sessionmaker[Session],
    *,
    actor_id: str | None,
) -> tuple[FastAPI, dict[str, int]]:
    counters = {"echo": 0, "fail": 0}
    app = FastAPI()
    app.state.session_factory = session_factory
    app.add_middleware(IdempotencyKeyMiddleware)
    if actor_id is not None:
        app.add_middleware(StubAuthMiddleware, actor_id=actor_id)

    @app.get("/v1/echo")
    def get_echo() -> dict[str, str]:
        return {"ok": "get"}

    @app.post("/v1/echo")
    def post_echo(payload: dict[str, object]) -> dict[str, object]:
        counters["echo"] += 1
        return {"count": counters["echo"], "payload": payload}

    @app.post("/v1/fail")
    def post_fail(payload: dict[str, object]) -> JSONResponse:
        counters["fail"] += 1
        return JSONResponse(
            status_code=422,
            content={"count": counters["fail"], "payload": payload},
        )

    return app, counters


def count_idempotency_rows(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
        return int(
            session.scalar(
                sa.select(sa.func.count()).select_from(TempIdempotencyKeyRecord)
            )
            or 0
        )


def latest_idempotency_row(
    session_factory: sessionmaker[Session], key: str
) -> TempIdempotencyKeyRecord:
    with session_factory() as session:
        record = session.get(TempIdempotencyKeyRecord, key)
        assert record is not None
        return record


def test_requires_idempotency_and_normalization_helpers() -> None:
    assert normalize_idempotency_key("550e8400-e29b-41d4-a716-446655440000")
    assert (
        normalize_idempotency_key("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    )

    with pytest.raises(ValueError, match="UUID or ULID"):
        normalize_idempotency_key("not-a-key")

    async def _receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    request_get = Request(
        {"type": "http", "method": "GET", "path": "/v1/tasks", "headers": []},
        receive=_receive,
    )
    request_post = Request(
        {"type": "http", "method": "POST", "path": "/v1/tasks", "headers": []},
        receive=_receive,
    )
    request_hidden = Request(
        {"type": "http", "method": "POST", "path": "/task-types", "headers": []},
        receive=_receive,
    )
    request_other = Request(
        {"type": "http", "method": "POST", "path": "/healthz", "headers": []},
        receive=_receive,
    )

    assert requires_idempotency(request_get) is False
    assert requires_idempotency(request_post) is True
    assert requires_idempotency(request_hidden) is True
    assert requires_idempotency(request_other) is False


def test_non_mutating_and_actorless_requests_bypass_cache(
    session_factory: sessionmaker[Session],
) -> None:
    actor_id = str(uuid.uuid4())
    seed_actor(session_factory, actor_id=actor_id)
    app, _ = build_echo_app(session_factory, actor_id=actor_id)
    with TestClient(app) as client:
        get_response = client.get("/v1/echo")
        assert get_response.status_code == 200
        assert get_response.json() == {"ok": "get"}
        assert count_idempotency_rows(session_factory) == 0

    actorless_app, counters = build_echo_app(session_factory, actor_id=None)
    with TestClient(actorless_app) as client:
        response = client.post(
            "/v1/echo",
            headers={IDEMPOTENCY_KEY_HEADER: str(uuid.uuid4())},
            json={"message": "hello"},
        )
        assert response.status_code == 200
        assert response.json()["count"] == 1
        assert counters["echo"] == 1
        assert count_idempotency_rows(session_factory) == 0


def test_missing_and_invalid_headers_return_400(
    session_factory: sessionmaker[Session],
) -> None:
    actor_id = str(uuid.uuid4())
    seed_actor(session_factory, actor_id=actor_id)
    app, _ = build_echo_app(session_factory, actor_id=actor_id)
    with TestClient(app) as client:
        missing = client.post("/v1/echo", json={"message": "missing"})
        assert missing.status_code == 400
        assert missing.json()["message"] == "Idempotency-Key header is required"

        empty = client.post(
            "/v1/echo",
            headers={IDEMPOTENCY_KEY_HEADER: "   "},
            json={"message": "empty"},
        )
        assert empty.status_code == 400
        assert empty.json()["message"] == "Idempotency-Key header is required"

        invalid = client.post(
            "/v1/echo",
            headers={IDEMPOTENCY_KEY_HEADER: "bad-key"},
            json={"message": "invalid"},
        )
        assert invalid.status_code == 400
        assert invalid.json()["message"] == "Idempotency-Key must be a UUID or ULID"


def test_miss_hit_conflict_and_expired_key_behaviors(
    session_factory: sessionmaker[Session],
) -> None:
    actor_id = str(uuid.uuid4())
    seed_actor(session_factory, actor_id=actor_id)
    app, counters = build_echo_app(session_factory, actor_id=actor_id)
    key = str(uuid.uuid4())
    with TestClient(app) as client:
        first = client.post(
            "/v1/echo",
            headers={IDEMPOTENCY_KEY_HEADER: key},
            json={"message": "hello"},
        )
        assert first.status_code == 200
        assert first.json()["count"] == 1
        assert counters["echo"] == 1
        assert count_idempotency_rows(session_factory) == 1

        second = client.post(
            "/v1/echo",
            headers={IDEMPOTENCY_KEY_HEADER: key},
            json={"message": "hello"},
        )
        assert second.status_code == 200
        assert second.headers[IDEMPOTENCY_REPLAYED_HEADER] == "true"
        assert second.json() == first.json()
        assert counters["echo"] == 1

        conflict = client.post(
            "/v1/echo",
            headers={IDEMPOTENCY_KEY_HEADER: key},
            json={"message": "different"},
        )
        assert conflict.status_code == 409
        assert conflict.headers[IDEMPOTENCY_CONFLICT_HEADER] == "true"
        assert counters["echo"] == 1

        with session_factory() as session:
            record = session.get(TempIdempotencyKeyRecord, key)
            assert record is not None
            record.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
            session.commit()

        third = client.post(
            "/v1/echo",
            headers={IDEMPOTENCY_KEY_HEADER: key},
            json={"message": "hello"},
        )
        assert third.status_code == 200
        assert third.json()["count"] == 2
        assert counters["echo"] == 2

    record = latest_idempotency_row(session_factory, key)
    assert record.replay_count == 0


def test_failed_mutations_do_not_cache_and_cleanup_stats_cover_module(
    session_factory: sessionmaker[Session],
) -> None:
    actor_id = str(uuid.uuid4())
    seed_actor(session_factory, actor_id=actor_id)
    app, counters = build_echo_app(session_factory, actor_id=actor_id)
    fail_key = str(uuid.uuid4())
    with TestClient(app) as client:
        first = client.post(
            "/v1/fail",
            headers={IDEMPOTENCY_KEY_HEADER: fail_key},
            json={"message": "bad"},
        )
        second = client.post(
            "/v1/fail",
            headers={IDEMPOTENCY_KEY_HEADER: fail_key},
            json={"message": "bad"},
        )
        assert first.status_code == 422
        assert second.status_code == 422
        assert counters["fail"] == 2
        assert count_idempotency_rows(session_factory) == 0

    with session_factory() as session:
        session.add(
            TempIdempotencyKeyRecord(
                key="01ARZ3NDEKTSV4RRFFQ69G5FAV",
                actor_id=actor_id,
                body_sha256=b"\x00" * 32,
                response_status=201,
                response_body=json.dumps({"ok": True}),
                replay_count=3,
                expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1),
            )
        )
        session.add(
            TempIdempotencyKeyRecord(
                key=str(uuid.uuid4()),
                actor_id=actor_id,
                body_sha256=b"\x01" * 32,
                response_status=201,
                response_body=json.dumps({"ok": True}),
                replay_count=1,
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
            )
        )
        session.commit()

    with session_factory() as session:
        stats = get_idempotency_stats(session)
        stats_json = json.loads(stats_as_json(stats))
        assert stats.hit_count == 4
        assert stats.miss_count == 2
        assert stats.expired_count == 1
        assert stats.active_count == 1
        assert stats_json == {
            "active_count": 1,
            "expired_count": 1,
            "hit_count": 4,
            "miss_count": 2,
        }

        deleted = cleanup_expired_idempotency_keys(session)
        session.commit()
        assert deleted == 1

    with session_factory() as session:
        post_cleanup = get_idempotency_stats(session)
        assert post_cleanup.expired_count == 0
        assert post_cleanup.active_count == 1
