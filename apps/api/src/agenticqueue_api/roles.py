"""Role CRUD helpers layered on top of the Phase 2 capability model."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from agenticqueue_api.capabilities import grant_capability
from agenticqueue_api.models import (
    ActorRecord,
    CapabilityGrantRecord,
    CapabilityRecord,
    RoleAssignmentModel,
    RoleAssignmentRecord,
    RoleModel,
    RoleName,
    RoleRecord,
)


def _build_role_model(record: RoleRecord) -> RoleModel:
    return RoleModel.model_validate(record)


def _build_role_assignment_model(
    assignment_record: RoleAssignmentRecord,
    role_record: RoleRecord,
) -> RoleAssignmentModel:
    role = _build_role_model(role_record)
    return RoleAssignmentModel(
        id=assignment_record.id,
        actor_id=assignment_record.actor_id,
        role_id=assignment_record.role_id,
        role_name=role.name,
        description=role.description,
        capabilities=role.capabilities,
        scope=role.scope,
        granted_by_actor_id=assignment_record.granted_by_actor_id,
        expires_at=assignment_record.expires_at,
        revoked_at=assignment_record.revoked_at,
        created_at=assignment_record.created_at,
        updated_at=assignment_record.updated_at,
    )


def list_roles(session: Session) -> list[RoleModel]:
    """Return the seeded role catalog."""

    statement = sa.select(RoleRecord).order_by(
        RoleRecord.name.asc(), RoleRecord.id.asc()
    )
    return [_build_role_model(record) for record in session.scalars(statement)]


def get_role(session: Session, *, role_name: RoleName | str) -> RoleModel | None:
    """Return one role by name."""

    record = session.scalar(
        sa.select(RoleRecord).where(RoleRecord.name == str(role_name).strip())
    )
    if record is None:
        return None
    return _build_role_model(record)


def _get_active_assignment_record(
    session: Session,
    *,
    actor_id: uuid.UUID,
    role_id: uuid.UUID,
    now: dt.datetime,
) -> RoleAssignmentRecord | None:
    statement = (
        sa.select(RoleAssignmentRecord)
        .where(
            RoleAssignmentRecord.actor_id == actor_id,
            RoleAssignmentRecord.role_id == role_id,
            RoleAssignmentRecord.revoked_at.is_(None),
            sa.or_(
                RoleAssignmentRecord.expires_at.is_(None),
                RoleAssignmentRecord.expires_at > now,
            ),
        )
        .order_by(
            RoleAssignmentRecord.created_at.asc(),
            RoleAssignmentRecord.id.asc(),
        )
    )
    return session.scalar(statement)


def _sync_assignment_capability_grants(
    session: Session,
    *,
    assignment_record: RoleAssignmentRecord,
    role: RoleModel,
    now: dt.datetime,
) -> None:
    existing_capabilities = {
        capability
        for capability in session.scalars(
            sa.select(CapabilityRecord.key)
            .join(
                CapabilityGrantRecord,
                CapabilityGrantRecord.capability_id == CapabilityRecord.id,
            )
            .where(
                CapabilityGrantRecord.role_assignment_id == assignment_record.id,
                CapabilityGrantRecord.revoked_at.is_(None),
                sa.or_(
                    CapabilityGrantRecord.expires_at.is_(None),
                    CapabilityGrantRecord.expires_at > now,
                ),
            )
        )
    }

    for capability in role.capabilities:
        if capability in existing_capabilities:
            continue
        grant_capability(
            session,
            actor_id=assignment_record.actor_id,
            capability=capability,
            scope=role.scope,
            granted_by_actor_id=assignment_record.granted_by_actor_id,
            expires_at=assignment_record.expires_at,
            role_assignment_id=assignment_record.id,
        )


def assign_role(
    session: Session,
    *,
    actor_id: uuid.UUID,
    role_name: RoleName | str,
    granted_by_actor_id: uuid.UUID | None = None,
    expires_at: dt.datetime | None = None,
) -> RoleAssignmentModel:
    """Assign one seeded role to an actor and materialize its capability grants."""

    current_time = dt.datetime.now(dt.UTC)
    if session.get(ActorRecord, actor_id) is None:
        raise ValueError("Actor not found")
    role_record = session.scalar(
        sa.select(RoleRecord).where(RoleRecord.name == str(role_name).strip())
    )
    if role_record is None:
        raise ValueError(f"Unknown role: {role_name}")

    assignment_record = _get_active_assignment_record(
        session,
        actor_id=actor_id,
        role_id=role_record.id,
        now=current_time,
    )
    if assignment_record is None:
        assignment_record = RoleAssignmentRecord(
            actor_id=actor_id,
            role_id=role_record.id,
            granted_by_actor_id=granted_by_actor_id,
            expires_at=expires_at,
            revoked_at=None,
        )
        session.add(assignment_record)
        session.flush()
        session.refresh(assignment_record)
    elif assignment_record.expires_at != expires_at:
        assignment_record.expires_at = expires_at
        session.execute(
            sa.update(CapabilityGrantRecord)
            .where(
                CapabilityGrantRecord.role_assignment_id == assignment_record.id,
                CapabilityGrantRecord.revoked_at.is_(None),
            )
            .values(expires_at=expires_at)
        )
        session.flush()
        session.refresh(assignment_record)

    role = _build_role_model(role_record)
    _sync_assignment_capability_grants(
        session,
        assignment_record=assignment_record,
        role=role,
        now=current_time,
    )
    session.flush()
    session.refresh(assignment_record)
    return _build_role_assignment_model(assignment_record, role_record)


def get_role_assignment(
    session: Session,
    assignment_id: uuid.UUID,
) -> RoleAssignmentModel | None:
    """Return one role assignment by id."""

    row = session.execute(
        sa.select(RoleAssignmentRecord, RoleRecord)
        .join(RoleRecord, RoleRecord.id == RoleAssignmentRecord.role_id)
        .where(RoleAssignmentRecord.id == assignment_id)
    ).first()
    if row is None:
        return None
    assignment_record, role_record = row
    return _build_role_assignment_model(assignment_record, role_record)


def revoke_role_assignment(
    session: Session,
    assignment_id: uuid.UUID,
    *,
    revoked_at: dt.datetime | None = None,
) -> RoleAssignmentModel | None:
    """Revoke one role assignment and the capability grants it created."""

    row = session.execute(
        sa.select(RoleAssignmentRecord, RoleRecord)
        .join(RoleRecord, RoleRecord.id == RoleAssignmentRecord.role_id)
        .where(RoleAssignmentRecord.id == assignment_id)
    ).first()
    if row is None:
        return None

    assignment_record, role_record = row
    revoked_timestamp = revoked_at or dt.datetime.now(dt.UTC)
    assignment_record.revoked_at = revoked_timestamp
    session.execute(
        sa.update(CapabilityGrantRecord)
        .where(
            CapabilityGrantRecord.role_assignment_id == assignment_id,
            CapabilityGrantRecord.revoked_at.is_(None),
        )
        .values(revoked_at=revoked_timestamp)
    )
    session.flush()
    session.refresh(assignment_record)
    return _build_role_assignment_model(assignment_record, role_record)


def list_role_assignments_for_actor(
    session: Session,
    actor_id: uuid.UUID,
    *,
    include_inactive: bool = False,
    now: dt.datetime | None = None,
) -> list[RoleAssignmentModel]:
    """Return one actor's role assignments."""

    current_time = now or dt.datetime.now(dt.UTC)
    statement = (
        sa.select(RoleAssignmentRecord, RoleRecord)
        .join(RoleRecord, RoleRecord.id == RoleAssignmentRecord.role_id)
        .where(RoleAssignmentRecord.actor_id == actor_id)
        .order_by(
            RoleAssignmentRecord.created_at.asc(),
            RoleAssignmentRecord.id.asc(),
        )
    )
    if not include_inactive:
        statement = statement.where(
            RoleAssignmentRecord.revoked_at.is_(None),
            sa.or_(
                RoleAssignmentRecord.expires_at.is_(None),
                RoleAssignmentRecord.expires_at > current_time,
            ),
        )

    return [
        _build_role_assignment_model(assignment_record, role_record)
        for assignment_record, role_record in session.execute(statement).all()
    ]
