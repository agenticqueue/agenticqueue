"""Local email/password auth helpers for human users."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import secrets
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from agenticqueue_api.models import UserRecord

SESSION_COOKIE_NAME = "aq_session"
CSRF_COOKIE_NAME = "csrf-token"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
PASSWORD_HASH_SCHEME = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 200_000


def normalize_email(email: str) -> str:
    """Normalize email identifiers before storage and comparison."""

    return email.strip().lower()


def hash_password(password: str) -> str:
    """Hash a local user password with stdlib PBKDF2-SHA256."""

    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PASSWORD_HASH_ITERATIONS,
    )
    return f"{PASSWORD_HASH_SCHEME}${PASSWORD_HASH_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, encoded_hash: str) -> bool:
    """Return whether a password matches a stored local user hash."""

    try:
        scheme, iterations_text, salt_hex, digest_hex = encoded_hash.split("$", 3)
        iterations = int(iterations_text)
    except ValueError:
        return False
    if scheme != PASSWORD_HASH_SCHEME or iterations < 1:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    ).hex()
    return hmac.compare_digest(actual, digest_hex)


def _hash_session_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _write_auth_audit(
    session: Session,
    *,
    action: str,
    user_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    ip_address: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.execute(
        sa.text("""
            INSERT INTO agenticqueue.auth_audit_log
                (user_id, actor_id, action, ip_address, details)
            VALUES
                (:user_id, :actor_id, :action, :ip_address, CAST(:details AS jsonb))
            """),
        {
            "user_id": user_id,
            "actor_id": actor_id,
            "action": action,
            "ip_address": ip_address,
            "details": json.dumps(details or {}),
        },
    )


def authenticate_email_password(
    session: Session,
    *,
    email: str,
    password: str,
) -> UserRecord | None:
    """Return an active local user if the supplied password is valid."""

    user = session.scalar(
        sa.select(UserRecord).where(
            UserRecord.email == normalize_email(email),
            UserRecord.is_active.is_(True),
        )
    )
    if user is None:
        return None
    if not verify_password(password, user.passcode_hash):
        return None
    return user


def create_browser_session(
    session: Session,
    *,
    user: UserRecord,
    ip_address: str | None,
) -> tuple[str, str]:
    """Create a local browser session and return raw session and CSRF tokens once."""

    raw_session_token = secrets.token_urlsafe(48)
    raw_csrf_token = secrets.token_urlsafe(32)
    now = dt.datetime.now(dt.UTC)
    expires_at = now + dt.timedelta(seconds=SESSION_MAX_AGE_SECONDS)
    session.execute(
        sa.text("""
            INSERT INTO agenticqueue.auth_sessions
                (
                    user_id,
                    session_token_hash,
                    csrf_token_hash,
                    expires_at,
                    revoked_at,
                    last_seen_at
                )
            VALUES
                (
                    :user_id,
                    :session_token_hash,
                    :csrf_token_hash,
                    :expires_at,
                    NULL,
                    :last_seen_at
                )
            """),
        {
            "user_id": user.id,
            "session_token_hash": _hash_session_token(raw_session_token),
            "csrf_token_hash": _hash_session_token(raw_csrf_token),
            "expires_at": expires_at,
            "last_seen_at": now,
        },
    )
    _write_auth_audit(
        session,
        action="LOGIN_SUCCEEDED",
        user_id=user.id,
        ip_address=ip_address,
        details={"email": user.email},
    )
    return raw_session_token, raw_csrf_token


def authenticate_browser_session(
    session: Session,
    raw_session_token: str | None,
    *,
    now: dt.datetime | None = None,
) -> UserRecord | None:
    """Return the active local user for a browser session cookie."""
    if not raw_session_token:
        return None

    current_time = now or dt.datetime.now(dt.UTC)
    session_token_hash = _hash_session_token(raw_session_token)
    row = session.execute(
        sa.text("""
            SELECT users.id AS user_id
            FROM agenticqueue.auth_sessions AS auth_sessions
            JOIN agenticqueue.users AS users
              ON users.id = auth_sessions.user_id
            WHERE auth_sessions.session_token_hash = :session_token_hash
              AND auth_sessions.revoked_at IS NULL
              AND auth_sessions.expires_at > :now
              AND users.is_active = true
            LIMIT 1
            """),
        {"session_token_hash": session_token_hash, "now": current_time},
    ).first()
    if row is None:
        return None

    session.execute(
        sa.text("""
            UPDATE agenticqueue.auth_sessions
            SET last_seen_at = :now
            WHERE session_token_hash = :session_token_hash
            """),
        {"session_token_hash": session_token_hash, "now": current_time},
    )
    return session.get(UserRecord, row.user_id)
