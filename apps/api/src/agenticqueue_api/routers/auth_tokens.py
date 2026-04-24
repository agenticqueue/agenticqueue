"""Dedicated auth/token routes kept separate from app composition wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import uuid

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from agenticqueue_api.auth import (
    get_api_token,
    issue_api_token,
    list_api_tokens_for_actor,
    revoke_api_token,
)
from agenticqueue_api.db import write_timeout
from agenticqueue_api.errors import raise_api_error
from agenticqueue_api.models import ActorRecord
from agenticqueue_api.pagination import DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT

if TYPE_CHECKING:
    from agenticqueue_api.app import (
        ApiTokenListResponse,
        ApiTokenView,
        ProvisionApiTokenRequest,
        ProvisionApiTokenResponse,
        RotateOwnKeyRequest,
    )


def build_auth_tokens_router(get_db_session: Any) -> APIRouter:
    """Build the dedicated auth/token router."""

    from agenticqueue_api import app as app_module

    globals()["ApiTokenListResponse"] = app_module.ApiTokenListResponse
    globals()["ApiTokenView"] = app_module.ApiTokenView
    globals()["ProvisionApiTokenRequest"] = app_module.ProvisionApiTokenRequest
    globals()["ProvisionApiTokenResponse"] = app_module.ProvisionApiTokenResponse
    globals()["RotateOwnKeyRequest"] = app_module.RotateOwnKeyRequest

    router = APIRouter()

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
            actor_exists = session.get(ActorRecord, payload.actor_id)
            if actor_exists is None:
                raise_api_error(status.HTTP_404_NOT_FOUND, "Actor not found")

            api_token, raw_token = issue_api_token(
                session,
                actor_id=payload.actor_id,
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
