"""Capability grant CRUD helpers and route dependencies."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
import uuid
from typing import Any

import sqlalchemy as sa
from fastapi import Body, Request, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from agenticqueue_api.errors import raise_api_error
from agenticqueue_api.models import (
    ActorModel,
    AuditLogRecord,
    CapabilityGrantModel,
    CapabilityGrantRecord,
    CapabilityKey,
    CapabilityRecord,
)

CapabilityScopeFunc = Callable[
    [Request, Session, dict[str, Any] | None, uuid.UUID | None],
    Mapping[str, Any] | None,
]


def _empty_scope(
    request: Request,
    session: Session,
    payload: dict[str, Any] | None,
    entity_id: uuid.UUID | None,
) -> Mapping[str, Any]:
    del request, session, payload, entity_id
    return {}


def _normalize_scope(scope: Mapping[str, Any] | None) -> dict[str, Any]:
    if not scope:
        return {}
    return dict(jsonable_encoder(dict(scope)))


def _grant_covers_scope(
    grant_scope: Mapping[str, Any] | None,
    required_scope: Mapping[str, Any],
) -> bool:
    normalized_grant_scope = _normalize_scope(grant_scope)
    for key, required_value in required_scope.items():
        if key not in normalized_grant_scope:
            continue
        if normalized_grant_scope[key] != required_value:
            return False
    return True


def _coerce_entity_id(raw_value: object) -> uuid.UUID | None:
    if isinstance(raw_value, uuid.UUID):
        return raw_value
    if raw_value is None:
        return None
    try:
        return uuid.UUID(str(raw_value))
    except (TypeError, ValueError):
        return None


def _capability_denial_details(
    capability: CapabilityKey,
    required_scope: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "missing_capability": capability.value,
        "required_scope": dict(required_scope),
    }


def _write_capability_denial(
    session: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID | None,
    capability: CapabilityKey,
    required_scope: Mapping[str, Any],
) -> None:
    session.execute(
        sa.insert(AuditLogRecord).values(
            actor_id=session.info.get("agenticqueue_audit_actor_id"),
            entity_type=entity_type,
            entity_id=entity_id,
            action="CAPABILITY_DENIED",
            before=None,
            after=_capability_denial_details(capability, required_scope),
            trace_id=session.info.get("agenticqueue_audit_trace_id"),
        )
    )
    # Capability denials must survive the request's eventual rollback path.
    session.commit()


def _build_capability_grant_model(
    grant_record: CapabilityGrantRecord,
    capability_record: CapabilityRecord,
) -> CapabilityGrantModel:
    return CapabilityGrantModel(
        id=grant_record.id,
        actor_id=grant_record.actor_id,
        capability_id=grant_record.capability_id,
        capability=capability_record.key,
        scope=grant_record.scope,
        granted_by_actor_id=grant_record.granted_by_actor_id,
        expires_at=grant_record.expires_at,
        revoked_at=grant_record.revoked_at,
        created_at=grant_record.created_at,
        updated_at=grant_record.updated_at,
    )


def grant_capability(
    session: Session,
    *,
    actor_id: uuid.UUID,
    capability: CapabilityKey,
    scope: dict[str, object] | None = None,
    granted_by_actor_id: uuid.UUID | None = None,
    expires_at: dt.datetime | None = None,
) -> CapabilityGrantModel:
    """Create a capability grant for an actor."""

    capability_record = session.scalar(
        sa.select(CapabilityRecord).where(CapabilityRecord.key == capability)
    )
    if capability_record is None:
        raise ValueError(f"Unknown capability: {capability}")

    grant_record = CapabilityGrantRecord(
        actor_id=actor_id,
        capability_id=capability_record.id,
        scope={} if scope is None else dict(scope),
        granted_by_actor_id=granted_by_actor_id,
        expires_at=expires_at,
        revoked_at=None,
    )
    session.add(grant_record)
    session.flush()
    session.refresh(grant_record)
    return _build_capability_grant_model(grant_record, capability_record)


def get_capability_grant(
    session: Session,
    grant_id: uuid.UUID,
) -> CapabilityGrantModel | None:
    """Fetch one capability grant by id."""

    row = session.execute(
        sa.select(CapabilityGrantRecord, CapabilityRecord)
        .join(
            CapabilityRecord, CapabilityRecord.id == CapabilityGrantRecord.capability_id
        )
        .where(CapabilityGrantRecord.id == grant_id)
    ).first()
    if row is None:
        return None
    grant_record, capability_record = row
    return _build_capability_grant_model(grant_record, capability_record)


def revoke_capability_grant(
    session: Session,
    grant_id: uuid.UUID,
    *,
    revoked_at: dt.datetime | None = None,
) -> CapabilityGrantModel | None:
    """Soft-revoke a capability grant."""

    row = session.execute(
        sa.select(CapabilityGrantRecord, CapabilityRecord)
        .join(
            CapabilityRecord, CapabilityRecord.id == CapabilityGrantRecord.capability_id
        )
        .where(CapabilityGrantRecord.id == grant_id)
    ).first()
    if row is None:
        return None

    grant_record, capability_record = row
    grant_record.revoked_at = revoked_at or dt.datetime.now(dt.UTC)
    session.flush()
    session.refresh(grant_record)
    return _build_capability_grant_model(grant_record, capability_record)


def list_capabilities_for_actor(
    session: Session,
    actor_id: uuid.UUID,
    *,
    include_inactive: bool = False,
    now: dt.datetime | None = None,
) -> list[CapabilityGrantModel]:
    """List capability grants for one actor."""

    current_time = now or dt.datetime.now(dt.UTC)
    statement = (
        sa.select(CapabilityGrantRecord, CapabilityRecord)
        .join(
            CapabilityRecord, CapabilityRecord.id == CapabilityGrantRecord.capability_id
        )
        .where(CapabilityGrantRecord.actor_id == actor_id)
        .order_by(
            CapabilityGrantRecord.created_at.asc(), CapabilityGrantRecord.id.asc()
        )
    )
    if not include_inactive:
        statement = statement.where(
            CapabilityGrantRecord.revoked_at.is_(None),
            sa.or_(
                CapabilityGrantRecord.expires_at.is_(None),
                CapabilityGrantRecord.expires_at > current_time,
            ),
        )

    return [
        _build_capability_grant_model(grant_record, capability_record)
        for grant_record, capability_record in session.execute(statement).all()
    ]


def ensure_actor_has_capability(
    session: Session,
    *,
    actor: ActorModel | None,
    capability: CapabilityKey,
    required_scope: Mapping[str, Any] | None = None,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
) -> None:
    """Raise unless the actor holds the required capability for the scope."""

    if actor is None:
        raise_api_error(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")

    if actor.actor_type == "admin":
        return

    normalized_scope = _normalize_scope(required_scope)
    for grant in list_capabilities_for_actor(session, actor.id):
        if grant.capability not in {capability, CapabilityKey.ADMIN}:
            continue
        if _grant_covers_scope(grant.scope, normalized_scope):
            return

    _write_capability_denial(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        capability=capability,
        required_scope=normalized_scope,
    )
    raise_api_error(
        status.HTTP_403_FORBIDDEN,
        "Capability grant required",
        details=_capability_denial_details(capability, normalized_scope),
    )


def require_capability(
    capability: CapabilityKey,
    scope_func: CapabilityScopeFunc | None = None,
    *,
    entity_type: str,
) -> Callable[..., None]:
    """Build a dependency that enforces one capability grant."""

    resolved_scope_func = scope_func or _empty_scope

    def dependency(
        request: Request,
        session: Session,
        payload: dict[str, Any] | None = Body(default=None),
        entity_id: uuid.UUID | None = None,
    ) -> None:
        actor = getattr(request.state, "actor", None)
        if actor is None:
            raise_api_error(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")

        required_scope = _normalize_scope(
            resolved_scope_func(request, session, payload, entity_id)
        )
        denied_entity_id = entity_id
        if denied_entity_id is None and payload is not None:
            denied_entity_id = _coerce_entity_id(payload.get("id"))
        ensure_actor_has_capability(
            session,
            actor=actor,
            capability=capability,
            required_scope=required_scope,
            entity_type=entity_type,
            entity_id=denied_entity_id,
        )

    dependency.__name__ = f"require_capability_{entity_type}_{capability.value}"
    return dependency
