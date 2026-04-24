"""First-run local-admin bootstrap routes."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Callable, Iterator
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import Field
from sqlalchemy.orm import Session

from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.config import get_admin_passcode
from agenticqueue_api.errors import raise_api_error
from agenticqueue_api.local_auth import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    create_browser_session,
    hash_password,
    normalize_email,
)
from agenticqueue_api.models import ActorRecord, UserRecord
from agenticqueue_api.models.shared import SchemaModel


class BootstrapStatusResponse(SchemaModel):
    """Whether the first local admin still needs to be bootstrapped."""

    needs_bootstrap: bool


class BootstrapAdminRequest(SchemaModel):
    """First-run local owner bootstrap payload."""

    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    passcode: str = Field(min_length=1)
    password: str = Field(min_length=1)


class BootstrapUserResponse(SchemaModel):
    """Local owner returned after bootstrap."""

    id: uuid.UUID
    email: str
    role: Literal["owner"] = "owner"


class BootstrapAdminResponse(SchemaModel):
    """Successful first-run bootstrap response."""

    user: BootstrapUserResponse
    first_token: str


def _user_count(session: Session) -> int:
    return int(session.scalar(sa.select(sa.func.count()).select_from(UserRecord)) or 0)


def _client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    if request.client is None:
        return None
    return request.client.host


def _admin_passcode_matches(candidate: str, expected: str) -> bool:
    candidate_digest = hashlib.sha256(candidate.encode("utf-8")).digest()
    expected_digest = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(candidate_digest, expected_digest)


def _take_bootstrap_lock(session: Session) -> None:
    session.execute(sa.text("SELECT pg_advisory_xact_lock(293, 20260424)"))


def _create_owner_user(
    session: Session,
    *,
    email: str,
    password: str,
) -> UserRecord:
    actor = ActorRecord(
        handle="admin",
        actor_type="admin",
        display_name="Admin",
        auth_subject=f"local:{email}",
        is_active=True,
    )
    session.add(actor)
    session.flush()

    user = UserRecord(
        email=email,
        passcode_hash=hash_password(password),
        actor_id=actor.id,
        is_admin=True,
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def build_bootstrap_router(
    get_db_session: Callable[..., Iterator[Session]],
) -> APIRouter:
    """Build first-run bootstrap routes."""

    router = APIRouter(prefix="/api/auth")

    @router.get(
        "/bootstrap_status",
        response_model=BootstrapStatusResponse,
    )
    def bootstrap_status(
        session: Session = Depends(get_db_session),
    ) -> BootstrapStatusResponse:
        return BootstrapStatusResponse(needs_bootstrap=_user_count(session) == 0)

    @router.post(
        "/bootstrap_admin",
        response_model=BootstrapAdminResponse,
    )
    def bootstrap_admin(
        payload: BootstrapAdminRequest,
        request: Request,
        response: Response,
        session: Session = Depends(get_db_session),
    ) -> BootstrapAdminResponse:
        _take_bootstrap_lock(session)
        if _user_count(session) > 0:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Bootstrap not found")

        expected_passcode = get_admin_passcode()
        if expected_passcode is None:
            raise_api_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "AQ_ADMIN_PASSCODE must be set before bootstrap",
            )
        if not _admin_passcode_matches(payload.passcode, expected_passcode):
            raise_api_error(
                status.HTTP_401_UNAUTHORIZED,
                "Invalid bootstrap passcode",
                error_code="auth_failed",
            )

        user = _create_owner_user(
            session,
            email=normalize_email(payload.email),
            password=payload.password,
        )
        assert user.actor_id is not None
        _, first_token = issue_api_token(
            session,
            actor_id=user.actor_id,
            scopes=["admin"],
            expires_at=None,
        )
        session_token, csrf_token = create_browser_session(
            session,
            user=user,
            ip_address=_client_ip(request),
        )
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session_token,
            max_age=SESSION_MAX_AGE_SECONDS,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
        response.set_cookie(
            CSRF_COOKIE_NAME,
            csrf_token,
            max_age=SESSION_MAX_AGE_SECONDS,
            path="/",
            secure=True,
            httponly=False,
            samesite="lax",
        )

        return BootstrapAdminResponse(
            user=BootstrapUserResponse(id=user.id, email=user.email),
            first_token=first_token,
        )

    return router
