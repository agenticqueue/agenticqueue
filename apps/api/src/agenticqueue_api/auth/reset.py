"""Passcode reset service for local human auth."""

from __future__ import annotations

from dataclasses import dataclass
import secrets
import string
import uuid
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from agenticqueue_api.auth.hashing import hash_passcode
from agenticqueue_api.models import (
    AuthAuditLogRecord,
    AuthSessionRecord,
    UserRecord,
)

PASSCODE_LENGTH = 20
PASSCODE_ALPHABET = string.ascii_letters + string.digits
PASSCODE_RESET_ACTION = "PASSCODE_RESET"


class UnknownUserError(ValueError):
    """Raised when a reset target cannot be found."""

    def __init__(self, username: str) -> None:
        super().__init__(f"Unknown user: {username}")
        self.username = username


class LastAdminResetRequiresForceError(ValueError):
    """Raised when resetting the final active admin without explicit force."""

    def __init__(self, username: str) -> None:
        super().__init__(f"Resetting last active admin '{username}' requires --force")
        self.username = username


@dataclass(frozen=True)
class PasscodeResetResult:
    """Public result returned exactly once to the caller."""

    username: str
    user_id: uuid.UUID
    passcode: str
    sessions_deleted: int

    def to_public_dict(self) -> dict[str, object]:
        return {
            "passcode": self.passcode,
            "sessions_deleted": self.sessions_deleted,
            "user_id": str(self.user_id),
            "username": self.username,
        }


def generate_passcode() -> str:
    """Generate one shell-safe 20-character passcode."""

    return "".join(secrets.choice(PASSCODE_ALPHABET) for _ in range(PASSCODE_LENGTH))


def _active_admin_count(session: Session) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(UserRecord)
            .where(
                UserRecord.is_admin.is_(True),
                UserRecord.is_active.is_(True),
            )
        )
        or 0
    )


def reset_user_passcode(
    session: Session,
    *,
    username: str,
    actor_id: uuid.UUID | None,
    method: Literal["api", "cli"],
    force: bool = False,
) -> PasscodeResetResult:
    """Rotate one user's passcode hash, delete sessions, and audit the reset."""

    target = session.scalar(
        sa.select(UserRecord).where(
            UserRecord.username == username,
            UserRecord.is_active.is_(True),
        )
    )
    if target is None:
        raise UnknownUserError(username)

    if target.is_admin and not force and _active_admin_count(session) <= 1:
        raise LastAdminResetRequiresForceError(username)

    passcode = generate_passcode()
    target.passcode_hash = hash_passcode(passcode)
    delete_result = session.execute(
        sa.delete(AuthSessionRecord).where(AuthSessionRecord.user_id == target.id)
    )
    sessions_deleted = max(0, int(delete_result.rowcount or 0))
    audit_actor_id = actor_id or target.actor_id
    session.add(
        AuthAuditLogRecord(
            user_id=target.id,
            actor_id=audit_actor_id,
            action=PASSCODE_RESET_ACTION,
            details={
                "method": method,
                "sessions_deleted": sessions_deleted,
                "target_user_id": str(target.id),
                "username": target.username,
            },
        )
    )
    session.flush()
    return PasscodeResetResult(
        username=target.username,
        user_id=target.id,
        passcode=passcode,
        sessions_deleted=sessions_deleted,
    )
