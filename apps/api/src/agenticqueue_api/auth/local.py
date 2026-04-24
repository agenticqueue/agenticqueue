"""Local username/passcode auth services."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from agenticqueue_api.auth import (
    SESSION_MAX_AGE_SECONDS,
    create_csrf_token,
    create_session_token,
    hash_session_token,
)
from agenticqueue_api.auth.hashing import hash_passcode, verify_passcode
from agenticqueue_api.config import get_admin_passcode
from agenticqueue_api.models import (
    ActorRecord,
    AuthAuditLogRecord,
    AuthRateLimitRecord,
    AuthSessionRecord,
    ProjectMemberRecord,
    ProjectRecord,
    UserRecord,
)

LOGIN_LIMIT = 5
LOGIN_WINDOW_SECONDS = 15 * 60


class AdminPasscodeMissingError(RuntimeError):
    """Raised when first boot has no configured admin passcode."""


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _window_start(now: dt.datetime) -> dt.datetime:
    minute = now.replace(second=0, microsecond=0)
    minute_bucket = minute.minute - (minute.minute % 15)
    return minute.replace(minute=minute_bucket)


def _write_auth_audit(
    session: Session,
    *,
    action: str,
    user_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    ip_address: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuthAuditLogRecord(
            user_id=user_id,
            actor_id=actor_id,
            action=action,
            ip_address=ip_address,
            details={} if details is None else details,
        )
    )


def ensure_admin_seed(session: Session) -> None:
    """Seed the first local super-admin once, failing closed without a passcode."""

    user_count = int(
        session.scalar(sa.select(sa.func.count()).select_from(UserRecord)) or 0
    )
    if user_count > 0:
        return

    passcode = get_admin_passcode()
    if passcode is None:
        raise AdminPasscodeMissingError(
            "AQ_ADMIN_PASSCODE is required before first boot when no users exist"
        )

    actor = session.scalar(sa.select(ActorRecord).where(ActorRecord.handle == "admin"))
    if actor is None:
        actor = ActorRecord(
            handle="admin",
            actor_type="admin",
            display_name="Admin",
            auth_subject="local:admin",
            is_active=True,
        )
        session.add(actor)
        session.flush()
    user = UserRecord(
        username="admin",
        passcode_hash=hash_passcode(passcode),
        actor_id=actor.id,
        is_admin=True,
        is_active=True,
    )
    session.add(user)
    session.flush()
    _write_auth_audit(
        session,
        action="ADMIN_SEEDED",
        user_id=user.id,
        details={"username": user.username},
    )


def login_rate_limit_status(
    session: Session,
    *,
    ip_address: str,
    now: dt.datetime | None = None,
) -> tuple[bool, int]:
    """Increment and return whether an IP may attempt login in this window."""

    current_time = now or _now()
    window = _window_start(current_time)
    statement = (
        insert(AuthRateLimitRecord)
        .values(ip=ip_address, window_start_minute=window, count=1)
        .on_conflict_do_update(
            index_elements=["ip", "window_start_minute"],
            set_={"count": AuthRateLimitRecord.count + 1},
        )
        .returning(AuthRateLimitRecord.count)
    )
    count = int(session.execute(statement).scalar_one())
    return count <= LOGIN_LIMIT, LOGIN_WINDOW_SECONDS


def cleanup_login_rate_limits(
    session: Session,
    *,
    now: dt.datetime | None = None,
) -> int:
    """Delete expired login rate-limit windows."""

    current_time = now or _now()
    cutoff = current_time - dt.timedelta(days=1)
    result = session.execute(
        sa.delete(AuthRateLimitRecord).where(
            AuthRateLimitRecord.window_start_minute < cutoff
        )
    )
    return int(result.rowcount or 0)


def authenticate_user_passcode(
    session: Session,
    *,
    username: str,
    passcode: str,
) -> UserRecord | None:
    """Return an active user if the supplied passcode is valid."""

    user = session.scalar(
        sa.select(UserRecord).where(
            UserRecord.username == username,
            UserRecord.is_active.is_(True),
        )
    )
    if user is None:
        return None
    if not verify_passcode(passcode, user.passcode_hash):
        return None
    return user


def create_browser_session(
    session: Session,
    *,
    user: UserRecord,
    ip_address: str | None,
) -> tuple[str, str, AuthSessionRecord]:
    """Create a cookie session and return raw session + CSRF tokens once."""

    raw_session_token = create_session_token()
    raw_csrf_token = create_csrf_token()
    current_time = _now()
    record = AuthSessionRecord(
        user_id=user.id,
        session_token_hash=hash_session_token(raw_session_token),
        csrf_token_hash=hash_session_token(raw_csrf_token),
        expires_at=current_time + dt.timedelta(seconds=SESSION_MAX_AGE_SECONDS),
        revoked_at=None,
        last_seen_at=current_time,
    )
    session.add(record)
    session.flush()
    _write_auth_audit(
        session,
        action="LOGIN_SUCCEEDED",
        user_id=user.id,
        ip_address=ip_address,
        details={"username": user.username},
    )
    return raw_session_token, raw_csrf_token, record


def revoke_browser_session(
    session: Session,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    ip_address: str | None,
) -> None:
    """Revoke one browser session."""

    record = session.get(AuthSessionRecord, session_id)
    if record is not None and record.revoked_at is None:
        record.revoked_at = _now()
        _write_auth_audit(
            session,
            action="LOGOUT_SUCCEEDED",
            user_id=user_id,
            ip_address=ip_address,
        )


def revoke_agent_token_audit(
    session: Session,
    *,
    actor_id: uuid.UUID,
    token_id: uuid.UUID,
) -> None:
    """Write an auth audit row for an agent-token revocation."""

    _write_auth_audit(
        session,
        action="TOKEN_REVOKED",
        actor_id=actor_id,
        details={"token_id": str(token_id)},
    )


def list_user_projects(session: Session, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Return projects visible to one local human user."""

    rows = session.execute(
        sa.select(ProjectRecord, ProjectMemberRecord)
        .join(ProjectMemberRecord, ProjectMemberRecord.project_id == ProjectRecord.id)
        .where(ProjectMemberRecord.user_id == user_id)
        .order_by(ProjectRecord.created_at.asc(), ProjectRecord.id.asc())
    ).all()
    return [
        {
            "id": str(project.id),
            "workspace_id": str(project.workspace_id),
            "slug": project.slug,
            "name": project.name,
            "description": project.description,
            "role": member.role,
        }
        for project, member in rows
    ]
