from __future__ import annotations

import datetime as dt
import secrets
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
import yaml  # type: ignore[import-untyped]
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from agenticqueue_api.errors import (
    error_payload,
    install_exception_handlers,
    raise_api_error,
)
from agenticqueue_api.middleware import (
    ContentSizeLimitMiddleware,
    IdempotencyKeyMiddleware,
    SecretRedactionMiddleware,
    idempotency as idempotency_module,
)
from agenticqueue_api.schemas.submit import TaskCompletionSubmission


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


class TempIdempotencyKeyRecord(TempBase):
    __tablename__ = "idempotency_key"

    key: Mapped[str] = mapped_column(sa.Text(), primary_key=True)
    actor_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
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


class HeaderAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, token_store: dict[str, dict[str, Any]]) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._token_store = token_store

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        authorization = request.headers.get("Authorization")
        if authorization is None:
            return JSONResponse(
                status_code=401,
                content=error_payload(
                    status_code=401,
                    message="Missing Authorization header",
                ),
                headers={"WWW-Authenticate": "Bearer"},
            )

        scheme, separator, credentials = authorization.partition(" ")
        if separator == "" or scheme.lower() != "bearer" or not credentials.strip():
            return JSONResponse(
                status_code=401,
                content=error_payload(
                    status_code=401,
                    message="Invalid bearer token",
                ),
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = credentials.strip()
        token_state = self._token_store.get(token)
        if token_state is None:
            return JSONResponse(
                status_code=401,
                content=error_payload(
                    status_code=401,
                    message="Invalid bearer token",
                ),
                headers={"WWW-Authenticate": "Bearer"},
            )

        if token_state["revoked"]:
            return JSONResponse(
                status_code=401,
                content=error_payload(
                    status_code=401,
                    message="Invalid bearer token",
                ),
                headers={"WWW-Authenticate": "Bearer"},
            )
        if token_state["expires_at"] is not None and token_state["expires_at"] <= dt.datetime.now(dt.UTC):
            return JSONResponse(
                status_code=401,
                content=error_payload(
                    status_code=401,
                    message="Invalid bearer token",
                ),
                headers={"WWW-Authenticate": "Bearer"},
            )

        request.state.actor = SimpleNamespace(
            id=token_state["actor_id"],
            actor_type=token_state["actor_type"],
        )
        return await call_next(request)


def _policy_dir(base: Path, *, hard_block_secrets: bool) -> Path:
    policy_dir = base / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "default-coding.policy.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "1.0.0",
                "hitl_required": True,
                "autonomy_tier": 3,
                "capabilities": ["read_repo", "write_branch"],
                "body": {"hard_block_secrets": hard_block_secrets},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return policy_dir


def build_firewall_app(
    session_factory: sessionmaker[Session],
    *,
    policy_dir: Path,
    token_store: dict[str, dict[str, Any]],
) -> FastAPI:
    app = FastAPI()
    app.state.session_factory = session_factory
    app.add_middleware(IdempotencyKeyMiddleware)
    app.add_middleware(HeaderAuthMiddleware, token_store=token_store)
    app.add_middleware(SecretRedactionMiddleware, policy_directory=policy_dir)
    app.add_middleware(ContentSizeLimitMiddleware)
    install_exception_handlers(app)

    @app.post("/v1/tasks/demo/complete")
    async def complete_task(
        request: Request,
        payload: TaskCompletionSubmission,
    ) -> dict[str, Any]:
        return {
            "actor_id": str(request.state.actor.id),
            "payload": payload.model_dump(mode="json"),
            "redaction": getattr(request.state, "secret_redaction_context", None),
        }

    @app.post("/v1/admin-only")
    async def admin_only(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        actor = request.state.actor
        if actor.actor_type != "admin":
            raise_api_error(status.HTTP_403_FORBIDDEN, "Admin actor required")
        return {"ok": True, "actor_id": str(actor.id)}

    return app


@pytest.fixture
def security_record_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        idempotency_module,
        "_record_type",
        lambda: TempIdempotencyKeyRecord,
    )


@pytest.fixture
def security_session_factory(
    security_record_patch: None,
) -> Iterator[sessionmaker[Session]]:
    engine = sa.create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TempBase.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()


@pytest.fixture
def token_store() -> dict[str, dict[str, Any]]:
    return {}


@pytest.fixture
def token_factory(
    token_store: dict[str, dict[str, Any]],
) -> Callable[..., tuple[SimpleNamespace, str]]:
    def _issue(
        *,
        handle: str,
        actor_type: str = "admin",
        expires_at: dt.datetime | None = None,
        revoked: bool = False,
    ) -> tuple[SimpleNamespace, str]:
        actor = SimpleNamespace(
            id=f"{handle}-{uuid.uuid4().hex[:8]}",
            actor_type=actor_type,
        )
        token = f"aq-test-{secrets.token_hex(12)}"
        token_store[token] = {
            "actor_id": actor.id,
            "actor_type": actor.actor_type,
            "expires_at": expires_at,
            "revoked": revoked,
        }
        return actor, token

    return _issue


@pytest.fixture
def firewall_app_factory(
    security_session_factory: sessionmaker[Session],
    token_store: dict[str, dict[str, Any]],
    tmp_path: Path,
) -> Callable[..., FastAPI]:
    def _build(*, hard_block_secrets: bool = True) -> FastAPI:
        return build_firewall_app(
            security_session_factory,
            policy_dir=_policy_dir(
                tmp_path / uuid.uuid4().hex,
                hard_block_secrets=hard_block_secrets,
            ),
            token_store=token_store,
        )

    return _build


@pytest.fixture
def submission_payload_factory() -> Callable[[], dict[str, Any]]:
    def _build() -> dict[str, Any]:
        return {
            "output": {
                "diff_url": "artifacts/diffs/aq-178.patch",
                "test_report": "artifacts/tests/aq-178-firewall.txt",
                "artifacts": [
                    {
                        "kind": "patch",
                        "uri": "artifacts/diffs/aq-178.patch",
                        "details": {"format": "unified-diff"},
                    }
                ],
                "learnings": [
                    {
                        "title": "Firewall rejects malformed submit payloads early",
                        "type": "pattern",
                        "what_happened": "A submit payload hit the middleware gate before task closeout.",
                        "what_learned": "The firewall layer should reject malformed or unsafe bodies before persistence.",
                        "action_rule": "Run submit middleware before route logic.",
                        "applies_when": "A mutating submit endpoint accepts JSON payloads from agents.",
                        "does_not_apply_when": "The route is read-only.",
                        "evidence": ["tests/security/firewall/test_firewall_vectors.py"],
                        "scope": "project",
                        "confidence": "confirmed",
                        "status": "active",
                        "owner": "agenticqueue-core",
                        "review_date": "2026-04-21",
                    }
                ],
            },
            "dod_results": [
                {
                    "item": "All attack vectors return structured errors.",
                    "checked": True,
                }
            ],
            "had_failure": False,
            "had_block": False,
            "had_retry": False,
        }

    return _build
