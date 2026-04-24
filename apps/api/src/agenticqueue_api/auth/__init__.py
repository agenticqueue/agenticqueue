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
from agenticqueue_api.errors import error_payload
from agenticqueue_api.auth.hashing import hash_token_secret, verify_token_secret
from agenticqueue_api.models import (
    ActorModel,
    ActorRecord,
    ApiTokenModel,
    ApiTokenRecord,
    AuthSessionRecord,
    UserModel,
    UserRecord,
)

TOKEN_PREFIX = "aq__"
TOKEN_HASH_PREFIX_LENGTH = 16
TOKEN_SEPARATOR = "_"
WWW_AUTHENTICATE_HEADER = {"WWW-Authenticate": "Bearer"}
ANONYMOUS_PATHS = {
    "/health",
    "/healthz",
    "/setup",
    "/v1/auth/login",
    "/v1/health",
}
SESSION_COOKIE_NAME = "aq_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7


@dataclass(frozen=True)
class AuthenticatedRequest:
    """Authenticated request context built from a bearer token."""

    actor: ActorModel
    api_token: ApiTokenModel


@dataclass(frozen=True)
class AuthenticatedUserSession:
    """Authenticated request context built from an HttpOnly session cookie."""

    user: UserModel
    actor: ActorModel | None
    session_id: uuid.UUID


class AuthenticationError(ValueError):
    """Raised when bearer authentication fails."""


def _hash_token_secret(raw_secret: str) -> str:
    return hash_token_secret(raw_secret)


def _legacy_hash_token_secret(raw_secret: str) -> str:
    return hmac.new(
        get_token_signing_secret().encode("utf-8"),
        raw_secret.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _is_legacy_token_hash(token_hash: str) -> bool:
    return len(token_hash) == 64 and all(
        character in "0123456789abcdef" for character in token_hash.lower()
    )


def _token_prefix_from_hash(token_hash: str) -> str:
    return hashlib.sha256(token_hash.encode("utf-8")).hexdigest()[
        :TOKEN_HASH_PREFIX_LENGTH
    ]


def token_display_prefix(token_hash: str) -> str:
    """Return the non-secret prefix shown in API responses."""
    return f"{TOKEN_PREFIX}{_token_prefix_from_hash(token_hash)}"


def _render_token(raw_secret: str, token_hash: str) -> str:
    del token_hash
    prefix = hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()[
        :TOKEN_HASH_PREFIX_LENGTH
    ]
    return f"{TOKEN_PREFIX}{prefix}{TOKEN_SEPARATOR}{raw_secret}"


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
    """Resolve a non-revoked bearer token to an authenticated actor context."""

    return resolve_api_token(
        session,
        token_value,
        now=now,
        include_revoked=False,
    )


def resolve_api_token(
    session: Session,
    token_value: str,
    *,
    now: dt.datetime | None = None,
    include_revoked: bool = False,
) -> AuthenticatedRequest | None:
    """Resolve a bearer token, optionally allowing already-revoked rows."""

    try:
        prefix, raw_secret = _parse_token(token_value)
    except AuthenticationError:
        return None

    raw_secret_prefix = hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()[
        :TOKEN_HASH_PREFIX_LENGTH
    ]
    legacy_hash = _legacy_hash_token_secret(raw_secret)
    legacy_prefix = legacy_hash[:TOKEN_HASH_PREFIX_LENGTH]
    if prefix not in {raw_secret_prefix, legacy_prefix}:
        return None

    statement = sa.select(ApiTokenRecord, ActorRecord).join(
        ActorRecord, ActorRecord.id == ApiTokenRecord.actor_id
    )
    if not include_revoked:
        statement = statement.where(ApiTokenRecord.revoked_at.is_(None))
    current_time = now or dt.datetime.now(dt.UTC)
    for token_record, actor_record in session.execute(statement).all():
        if (
            token_record.expires_at is not None
            and token_record.expires_at <= current_time
        ):
            continue
        if verify_token_secret(raw_secret, token_record.token_hash):
            return AuthenticatedRequest(
                actor=ActorModel.model_validate(actor_record),
                api_token=ApiTokenModel.model_validate(token_record),
            )
        if not (
            _is_legacy_token_hash(token_record.token_hash)
            and hmac.compare_digest(token_record.token_hash, legacy_hash)
        ):
            continue

        token_record.token_hash = _hash_token_secret(raw_secret)
        session.flush()

        return AuthenticatedRequest(
            actor=ActorModel.model_validate(actor_record),
            api_token=ApiTokenModel.model_validate(token_record),
        )

    return None


def hash_session_token(raw_token: str) -> str:
    """Return the deterministic lookup hash for one opaque session token."""

    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_session_token() -> str:
    """Create a new opaque browser session token."""

    return secrets.token_urlsafe(48)


def create_csrf_token() -> str:
    """Create a new double-submit CSRF token."""

    return secrets.token_urlsafe(32)


def authenticate_session_cookie(
    session: Session,
    session_token: str | None,
    *,
    now: dt.datetime | None = None,
) -> AuthenticatedUserSession | None:
    """Resolve a browser session cookie to a local user context."""

    if session_token is None or not session_token.strip():
        return None

    current_time = now or dt.datetime.now(dt.UTC)
    row = session.execute(
        sa.select(AuthSessionRecord, UserRecord, ActorRecord)
        .join(UserRecord, UserRecord.id == AuthSessionRecord.user_id)
        .outerjoin(ActorRecord, ActorRecord.id == UserRecord.actor_id)
        .where(
            AuthSessionRecord.session_token_hash == hash_session_token(session_token),
            AuthSessionRecord.revoked_at.is_(None),
            AuthSessionRecord.expires_at > current_time,
            UserRecord.is_active.is_(True),
        )
    ).first()
    if row is None:
        return None

    session_record, user_record, actor_record = row
    session_record.last_seen_at = current_time
    session.flush()
    return AuthenticatedUserSession(
        user=UserModel.model_validate(user_record),
        actor=None if actor_record is None else ActorModel.model_validate(actor_record),
        session_id=session_record.id,
    )


def verify_session_csrf_token(
    session: Session,
    *,
    session_token: str | None,
    csrf_token: str | None,
    now: dt.datetime | None = None,
) -> bool:
    """Return whether the CSRF token belongs to the active session."""

    if (
        session_token is None
        or csrf_token is None
        or not session_token.strip()
        or not csrf_token.strip()
    ):
        return False

    current_time = now or dt.datetime.now(dt.UTC)
    record = session.scalar(
        sa.select(AuthSessionRecord).where(
            AuthSessionRecord.session_token_hash == hash_session_token(session_token),
            AuthSessionRecord.csrf_token_hash == hash_session_token(csrf_token),
            AuthSessionRecord.revoked_at.is_(None),
            AuthSessionRecord.expires_at > current_time,
        )
    )
    return record is not None


def _unauthorized_response(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content=error_payload(status_code=401, message=detail),
        headers=WWW_AUTHENTICATE_HEADER,
    )


class AgenticQueueAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate requests with bearer tokens or local browser sessions."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.url.path in ANONYMOUS_PATHS or (
            request.method == "DELETE"
            and request.url.path.startswith("/v1/auth/tokens/")
        ):
            return await call_next(request)

        session = request.app.state.session_factory()
        try:
            authorization = request.headers.get("Authorization")
            if authorization is not None:
                bearer_token = extract_bearer_token(authorization)
                authenticated = authenticate_api_token(session, bearer_token)
                if authenticated is None:
                    return _unauthorized_response("Invalid bearer token")

                request.state.actor = authenticated.actor
                request.state.api_token = authenticated.api_token
                session.commit()
                return await call_next(request)

            user_session = authenticate_session_cookie(
                session,
                request.cookies.get(SESSION_COOKIE_NAME),
            )
            if user_session is None:
                return _unauthorized_response("Missing Authorization header")

            request.state.user = user_session.user
            request.state.auth_session_id = user_session.session_id
            if user_session.actor is not None:
                request.state.actor = user_session.actor
            session.commit()
            return await call_next(request)
        except AuthenticationError as error:
            return _unauthorized_response(str(error))
        finally:
            session.rollback()
            session.close()
