"""Dedicated auth/token routes kept separate from app composition wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import uuid

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from agenticqueue_api.auth import (
    delete_api_token,
    get_api_token,
    issue_api_token,
    list_api_tokens_for_actor,
    revoke_api_token,
)
from agenticqueue_api.db import write_timeout
from agenticqueue_api.errors import raise_api_error
from agenticqueue_api.local_auth import (
    SESSION_COOKIE_NAME,
    authenticate_browser_session,
)
from agenticqueue_api.models import ActorRecord, UserRecord
from agenticqueue_api.pagination import DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT

if TYPE_CHECKING:
    from agenticqueue_api.app import (
        ApiTokenListResponse,
        ApiTokenView,
        BrowserTokenCreateRequest,
        BrowserTokenCreateResponse,
        BrowserTokenListResponse,
        ProvisionApiTokenRequest,
        ProvisionApiTokenResponse,
        RotateOwnKeyRequest,
    )


def build_auth_tokens_router(get_db_session: Any) -> APIRouter:
    """Build the dedicated auth/token router."""

    from agenticqueue_api import app as app_module

    globals()["ApiTokenListResponse"] = app_module.ApiTokenListResponse
    globals()["ApiTokenView"] = app_module.ApiTokenView
    globals()["BrowserTokenCreateRequest"] = app_module.BrowserTokenCreateRequest
    globals()["BrowserTokenCreateResponse"] = app_module.BrowserTokenCreateResponse
    globals()["BrowserTokenListResponse"] = app_module.BrowserTokenListResponse
    globals()["ProvisionApiTokenRequest"] = app_module.ProvisionApiTokenRequest
    globals()["ProvisionApiTokenResponse"] = app_module.ProvisionApiTokenResponse
    globals()["RotateOwnKeyRequest"] = app_module.RotateOwnKeyRequest

    router = APIRouter()

    def require_admin_browser_user(
        request: Request,
        session: Session,
    ) -> UserRecord:
        user = authenticate_browser_session(
            session,
            request.cookies.get(SESSION_COOKIE_NAME),
        )
        if user is None:
            raise_api_error(status.HTTP_401_UNAUTHORIZED, "Invalid browser session")
        if not user.is_admin or user.actor_id is None:
            raise_api_error(status.HTTP_403_FORBIDDEN, "Admin user required")
        return user

    @router.get("/api/auth/tokens", response_model=BrowserTokenListResponse)
    def list_browser_tokens(
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> BrowserTokenListResponse:
        user = require_admin_browser_user(request, session)
        assert user.actor_id is not None
        return BrowserTokenListResponse(
            tokens=[
                app_module._token_view(token)
                for token in list_api_tokens_for_actor(session, user.actor_id)
            ],
        )

    @router.post("/api/auth/tokens", response_model=BrowserTokenCreateResponse)
    def create_browser_token(
        payload: BrowserTokenCreateRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> BrowserTokenCreateResponse:
        name = payload.name.strip()
        if not name:
            raise_api_error(status.HTTP_422_UNPROCESSABLE_ENTITY, "Token name required")
        with write_timeout(session, endpoint="api.auth.tokens.create"):
            user = require_admin_browser_user(request, session)
            assert user.actor_id is not None
            api_token, raw_token = issue_api_token(
                session,
                actor_id=user.actor_id,
                name=name,
                scopes=["admin"],
                expires_at=None,
            )
            view = app_module._token_view(api_token)
            return BrowserTokenCreateResponse(
                **view.model_dump(),
                token=raw_token,
            )

    @router.delete(
        "/api/auth/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT
    )
    def delete_browser_token(
        token_id: uuid.UUID,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> Response:
        with write_timeout(session, endpoint="api.auth.tokens.delete"):
            user = require_admin_browser_user(request, session)
            assert user.actor_id is not None
            existing = get_api_token(session, token_id)
            if existing is None or existing.actor_id != user.actor_id:
                raise_api_error(status.HTTP_404_NOT_FOUND, "Token not found")
            deleted = delete_api_token(session, token_id)
            if deleted is None:
                raise_api_error(status.HTTP_404_NOT_FOUND, "Token not found")
            return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/v1/auth/tokens", response_model=ApiTokenListResponse)
    def list_tokens(
        request: Request,
        response: Response,
        limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
        cursor: str | None = Query(default=None),
        session: Session = Depends(get_db_session),
    ) -> ApiTokenListResponse:
        actor = app_module._require_actor(request)
        tokens = list_api_tokens_for_actor(session, actor.id)
        page = app_module._paginate_sequence(
            tokens,
            response=response,
            limit=limit,
            cursor=cursor,
            key_types=[str, str],
            key_fn=lambda token: [token.created_at.isoformat(), str(token.id)],
        )
        return ApiTokenListResponse(
            actor=app_module._actor_summary(actor),
            tokens=[app_module._token_view(token) for token in page],
        )

    @router.post(
        "/v1/auth/tokens",
        response_model=ProvisionApiTokenResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def provision_token(
        payload: ProvisionApiTokenRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> ProvisionApiTokenResponse:
        with write_timeout(session, endpoint="v1.auth.tokens.provision"):
            app_module._require_admin_actor(request)
            name = payload.name.strip()
            if not name:
                raise_api_error(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "Token name required",
                )
            actor_exists = session.get(ActorRecord, payload.actor_id)
            if actor_exists is None:
                raise_api_error(status.HTTP_404_NOT_FOUND, "Actor not found")

            api_token, raw_token = issue_api_token(
                session,
                actor_id=payload.actor_id,
                name=name,
                scopes=payload.scopes,
                expires_at=payload.expires_at,
            )
            return ProvisionApiTokenResponse(
                token=raw_token,
                api_token=app_module._token_view(api_token),
            )

    @router.post(
        "/v1/actors/me/rotate-key",
        response_model=ProvisionApiTokenResponse,
    )
    def rotate_own_key_endpoint(
        request: Request,
        session: Session = Depends(get_db_session),
        payload: RotateOwnKeyRequest | None = Body(default=None),
    ) -> ProvisionApiTokenResponse:
        with write_timeout(session, endpoint="v1.actors.me.rotate_key"):
            actor = app_module._require_actor(request)
            current_api_token = app_module._require_api_token(request)
            revoke_api_token(session, current_api_token.id)
            api_token, raw_token = issue_api_token(
                session,
                actor_id=actor.id,
                name=current_api_token.name,
                scopes=(
                    current_api_token.scopes
                    if payload is None or payload.scopes is None
                    else payload.scopes
                ),
                expires_at=None if payload is None else payload.expires_at,
            )
            return ProvisionApiTokenResponse(
                token=raw_token,
                api_token=app_module._token_view(api_token),
            )

    @router.post(
        "/v1/auth/tokens/{token_id}/revoke",
        response_model=ApiTokenView,
    )
    def revoke_token(
        token_id: uuid.UUID,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> ApiTokenView:
        with write_timeout(session, endpoint="v1.auth.tokens.revoke"):
            actor = app_module._require_actor(request)
            existing = get_api_token(session, token_id)
            if existing is None:
                raise_api_error(status.HTTP_404_NOT_FOUND, "Token not found")
            if actor.actor_type != "admin" and existing.actor_id != actor.id:
                raise_api_error(status.HTTP_404_NOT_FOUND, "Token not found")

            revoked = revoke_api_token(session, token_id)
            if revoked is None:
                raise_api_error(status.HTTP_404_NOT_FOUND, "Token not found")
            return app_module._token_view(revoked)

    return router


__all__ = ["build_auth_tokens_router"]
