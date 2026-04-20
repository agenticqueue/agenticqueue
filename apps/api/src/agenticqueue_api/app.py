"""FastAPI app for the AgenticQueue API token surface."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from typing import cast

import sqlalchemy as sa
from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import Field
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.auth import (
    AgenticQueueAuthMiddleware,
    get_api_token,
    issue_api_token,
    list_api_tokens_for_actor,
    revoke_api_token,
    token_display_prefix,
)
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import ActorModel, ActorRecord, ApiTokenModel
from agenticqueue_api.models.shared import SchemaModel


class ActorSummary(SchemaModel):
    """Compact actor payload surfaced from authenticated requests."""

    id: uuid.UUID
    handle: str
    actor_type: str
    display_name: str


class ApiTokenView(SchemaModel):
    """Non-secret token metadata returned from the API."""

    id: uuid.UUID
    actor_id: uuid.UUID
    token_prefix: str
    scopes: list[str]
    expires_at: dt.datetime | None = None
    revoked_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class ApiTokenListResponse(SchemaModel):
    """Token list for the authenticated actor."""

    actor: ActorSummary
    tokens: list[ApiTokenView]


class ProvisionApiTokenRequest(SchemaModel):
    """Payload for issuing a token to an actor."""

    actor_id: uuid.UUID
    scopes: list[str] = Field(default_factory=list)
    expires_at: dt.datetime | None = None


class ProvisionApiTokenResponse(SchemaModel):
    """Provisioning response including the raw token once."""

    token: str
    api_token: ApiTokenView


def _default_session_factory() -> sessionmaker[Session]:
    engine = sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _actor_summary(actor: ActorModel) -> ActorSummary:
    return ActorSummary(
        id=actor.id,
        handle=actor.handle,
        actor_type=actor.actor_type,
        display_name=actor.display_name,
    )


def _token_view(token: ApiTokenModel) -> ApiTokenView:
    return ApiTokenView(
        id=token.id,
        actor_id=token.actor_id,
        token_prefix=token_display_prefix(token.token_hash),
        scopes=token.scopes,
        expires_at=token.expires_at,
        revoked_at=token.revoked_at,
        created_at=token.created_at,
        updated_at=token.updated_at,
    )


def _require_actor(request: Request) -> ActorModel:
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token"
        )
    return cast(ActorModel, actor)


def _require_admin_actor(request: Request) -> ActorModel:
    actor = _require_actor(request)
    if actor.actor_type != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin actor required",
        )
    return actor


def get_db_session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_app(
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    """Create the FastAPI app with auth middleware and token endpoints."""
    app = FastAPI(
        title="AgenticQueue API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.session_factory = session_factory or _default_session_factory()
    app.add_middleware(AgenticQueueAuthMiddleware)

    @app.get("/v1/auth/tokens", response_model=ApiTokenListResponse)
    def list_tokens(
        request: Request, session: Session = Depends(get_db_session)
    ) -> ApiTokenListResponse:
        actor = _require_actor(request)
        tokens = list_api_tokens_for_actor(session, actor.id)
        return ApiTokenListResponse(
            actor=_actor_summary(actor),
            tokens=[_token_view(token) for token in tokens],
        )

    @app.post(
        "/v1/auth/tokens",
        response_model=ProvisionApiTokenResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def provision_token(
        payload: ProvisionApiTokenRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> ProvisionApiTokenResponse:
        _require_admin_actor(request)
        actor_exists = session.get(ActorRecord, payload.actor_id)
        if actor_exists is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Actor not found",
            )

        api_token, raw_token = issue_api_token(
            session,
            actor_id=payload.actor_id,
            scopes=payload.scopes,
            expires_at=payload.expires_at,
        )
        return ProvisionApiTokenResponse(
            token=raw_token, api_token=_token_view(api_token)
        )

    @app.post("/v1/auth/tokens/{token_id}/revoke", response_model=ApiTokenView)
    def revoke_token(
        token_id: uuid.UUID,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> ApiTokenView:
        actor = _require_actor(request)
        existing = get_api_token(session, token_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Token not found",
            )
        if actor.actor_type != "admin" and existing.actor_id != actor.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Token not found",
            )

        revoked = revoke_api_token(session, token_id)
        if revoked is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Token not found",
            )
        return _token_view(revoked)

    return app


app = create_app()
