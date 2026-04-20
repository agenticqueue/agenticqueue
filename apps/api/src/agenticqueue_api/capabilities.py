"""Capability grant CRUD helpers."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from agenticqueue_api.models import (
    CapabilityGrantModel,
    CapabilityGrantRecord,
    CapabilityKey,
    CapabilityRecord,
)


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
