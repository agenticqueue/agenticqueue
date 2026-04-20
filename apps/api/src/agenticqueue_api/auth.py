"""API token issuance and Bearer authentication helpers."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from agenticqueue_api.config import get_token_signing_secret
from agenticqueue_api.models import (
    ActorModel,
    ActorRecord,
    ApiTokenModel,
    ApiTokenRecord,
)

TOKEN_PREFIX = "aq__"
TOKEN_HASH_PREFIX_LENGTH = 16
TOKEN_SEPARATOR = "_"
WWW_AUTHENTICATE_HEADER = {"WWW-Authenticate": "Bearer"}


@dataclass(frozen=True)
class AuthenticatedRequest:
    """Authenticated request context built from a bearer token."""

    actor: ActorModel
    api_token: ApiTokenModel


class AuthenticationError(ValueError):
    """Raised when bearer authentication fails."""


def _hash_token_secret(raw_secret: str) -> str:
    digest = hmac.new(
        get_token_signing_secret().encode("utf-8"),
        raw_secret.encode("utf-8"),
        hashlib.sha256,
    )
    return digest.hexdigest()


def _token_prefix_from_hash(token_hash: str) -> str:
    return token_hash[:TOKEN_HASH_PREFIX_LENGTH]


def token_display_prefix(token_hash: str) -> str:
    """Return the non-secret prefix shown in API responses."""
    return f"{TOKEN_PREFIX}{_token_prefix_from_hash(token_hash)}"


def _render_token(raw_secret: str, token_hash: str) -> str:
    return f"{TOKEN_PREFIX}{_token_prefix_from_hash(token_hash)}{TOKEN_SEPARATOR}{raw_secret}"


def extract_bearer_token(authorization_header: str | None) -> str:
    """Extract a bearer token from the Authorization header."""
    if authorization_header is None:
        raise AuthenticationError("Missing Authorization header")

    scheme, separator, credentials = authorization_header.partition(" ")
    if separator == "" or scheme.lower() != "bearer" or not credentials.strip():
        raise AuthenticationError("Invalid bearer token")
    return credentials.strip()


def _parse_token(token_value: str) -> tuple[str, str]:
    if not token_value.startswith(TOKEN_PREFIX):
        raise AuthenticationError("Invalid bearer token")

    prefix, separator, raw_secret = token_value[len(TOKEN_PREFIX) :].partition(
        TOKEN_SEPARATOR
    )
    if (
        separator == ""
        or len(prefix) != TOKEN_HASH_PREFIX_LENGTH
        or not raw_secret
        or any(character not in "0123456789abcdef" for character in prefix.lower())
    ):
        raise AuthenticationError("Invalid bearer token")
    return prefix.lower(), raw_secret


def issue_api_token(
    session: Session,
    *,
    actor_id: uuid.UUID,
    scopes: list[str],
    expires_at: dt.datetime | None,
) -> tuple[ApiTokenModel, str]:
    """Create an API token row and return the raw token once."""
    raw_secret = secrets.token_hex(32)
    normalized_scopes = list(
        dict.fromkeys(scope.strip() for scope in scopes if scope.strip())
    )
    token_hash = _hash_token_secret(raw_secret)

    record = ApiTokenRecord(
        actor_id=actor_id,
        token_hash=token_hash,
        scopes=normalized_scopes,
        expires_at=expires_at,
        revoked_at=None,
    )
    session.add(record)
    session.flush()
    session.refresh(record)
    return ApiTokenModel.model_validate(record), _render_token(raw_secret, token_hash)


def list_api_tokens_for_actor(
    session: Session, actor_id: uuid.UUID
) -> list[ApiTokenModel]:
    """Return all API tokens issued for an actor."""
    statement = (
        sa.select(ApiTokenRecord)
        .where(ApiTokenRecord.actor_id == actor_id)
        .order_by(ApiTokenRecord.created_at.asc(), ApiTokenRecord.id.asc())
    )
    return [
        ApiTokenModel.model_validate(record) for record in session.scalars(statement)
    ]


def get_api_token(session: Session, token_id: uuid.UUID) -> ApiTokenModel | None:
    """Fetch one API token by id."""
    record = session.get(ApiTokenRecord, token_id)
    if record is None:
        return None
    return ApiTokenModel.model_validate(record)


def revoke_api_token(
    session: Session,
    token_id: uuid.UUID,
    *,
    revoked_at: dt.datetime | None = None,
) -> ApiTokenModel | None:
    """Mark an API token as revoked."""
    record = session.get(ApiTokenRecord, token_id)
    if record is None:
        return None
    record.revoked_at = revoked_at or dt.datetime.now(dt.UTC)
    session.flush()
    session.refresh(record)
    return ApiTokenModel.model_validate(record)


def authenticate_api_token(
    session: Session,
    token_value: str,
    *,
    now: dt.datetime | None = None,
) -> AuthenticatedRequest | None:
    """Resolve a bearer token to an authenticated actor context."""
    try:
        prefix, raw_secret = _parse_token(token_value)
    except AuthenticationError:
        return None

    token_hash = _hash_token_secret(raw_secret)
    if not hmac.compare_digest(prefix, _token_prefix_from_hash(token_hash)):
        return None

    statement = (
        sa.select(ApiTokenRecord, ActorRecord)
        .join(ActorRecord, ActorRecord.id == ApiTokenRecord.actor_id)
        .where(
            ApiTokenRecord.token_hash.like(f"{prefix}%"),
            ApiTokenRecord.token_hash == token_hash,
        )
    )
    row = session.execute(statement).first()
    if row is None:
        return None

    token_record, actor_record = row
    current_time = now or dt.datetime.now(dt.UTC)
    if token_record.revoked_at is not None:
        return None
    if token_record.expires_at is not None and token_record.expires_at <= current_time:
        return None

    return AuthenticatedRequest(
        actor=ActorModel.model_validate(actor_record),
        api_token=ApiTokenModel.model_validate(token_record),
    )


def _unauthorized_response(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": detail},
        headers=WWW_AUTHENTICATE_HEADER,
    )


class AgenticQueueAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate every request with an AgenticQueue bearer token."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        session = request.app.state.session_factory()
        try:
            bearer_token = extract_bearer_token(request.headers.get("Authorization"))
            authenticated = authenticate_api_token(session, bearer_token)
            if authenticated is None:
                return _unauthorized_response("Invalid bearer token")

            request.state.actor = authenticated.actor
            request.state.api_token = authenticated.api_token
            return await call_next(request)
        except AuthenticationError as error:
            return _unauthorized_response(str(error))
        finally:
            session.close()
