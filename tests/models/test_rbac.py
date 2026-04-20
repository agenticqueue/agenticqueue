from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.capabilities import list_capabilities_for_actor
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import ActorModel, CapabilityKey, CapabilityRecord
from agenticqueue_api.models.role import RoleName, STANDARD_ROLE_DEFINITIONS
from agenticqueue_api.repo import create_actor
from agenticqueue_api.roles import (
    assign_role,
    list_role_assignments_for_actor,
    list_roles,
    revoke_role_assignment,
)

TRUNCATE_TABLES = [
    "api_token",
    "capability_grant",
    "actor_role_assignment",
    "idempotency_key",
    "edge",
    "artifact",
    "decision",
    "run",
    "packet_version",
    "learning_drafts",
    "learning",
    "memory_item",
    "task",
    "project",
    "policy",
    "capability",
    "audit_log",
    "workspace",
    "actor",
]


def actor_id_for(handle: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{handle}")


def make_actor_payload(
    *,
    handle: str,
    actor_type: str,
    display_name: str,
) -> ActorModel:
    actor_id = actor_id_for(handle)
    payload = {
        "id": str(actor_id),
        "handle": handle,
        "actor_type": actor_type,
        "display_name": display_name,
        "auth_subject": f"{handle}-subject",
        "is_active": True,
        "created_at": "2026-04-20T00:00:00+00:00",
        "updated_at": "2026-04-20T00:00:00+00:00",
    }
    return ActorModel.model_validate_json(json.dumps(payload))


def truncate_all_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        qualified_tables = ", ".join(
            f"agenticqueue.{table_name}" for table_name in TRUNCATE_TABLES
        )
        connection.execute(
            sa.text(f"TRUNCATE TABLE {qualified_tables} RESTART IDENTITY CASCADE")
        )
        connection.execute(
            sa.insert(CapabilityRecord),
            [
                {
                    "key": capability,
                    "description": f"Seeded capability: {capability.value}",
                }
                for capability in CapabilityKey
            ],
        )


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> Iterator[None]:
    truncate_all_tables(engine)
    yield


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def seed_actor(
    session_factory: sessionmaker[Session],
    payload: ActorModel,
) -> ActorModel:
    with session_factory() as session:
        actor = create_actor(session, payload)
        session.commit()
        return actor


def test_seeded_roles_match_the_standard_catalog(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        roles = {role.name: role for role in list_roles(session)}

    assert set(roles) == {role_name.value for role_name in RoleName}
    for role_name, definition in STANDARD_ROLE_DEFINITIONS.items():
        role = roles[role_name.value]
        assert role.description == definition["description"]
        assert role.scope == definition["scope"]
        assert role.capabilities == list(definition["capabilities"])


def test_assigning_admin_role_materializes_all_current_capabilities_and_revokes_cleanly(
    session_factory: sessionmaker[Session],
) -> None:
    admin_actor = seed_actor(
        session_factory,
        make_actor_payload(
            handle="rbac-admin",
            actor_type="admin",
            display_name="RBAC Admin",
        ),
    )
    target_actor = seed_actor(
        session_factory,
        make_actor_payload(
            handle="rbac-target",
            actor_type="agent",
            display_name="RBAC Target",
        ),
    )

    with session_factory() as session:
        assignment = assign_role(
            session,
            actor_id=target_actor.id,
            role_name=RoleName.ADMIN,
            granted_by_actor_id=admin_actor.id,
        )
        session.commit()

    with session_factory() as session:
        assignments = list_role_assignments_for_actor(session, target_actor.id)
        grants = list_capabilities_for_actor(session, target_actor.id)

    assert len(assignments) == 1
    assert assignments[0].id == assignment.id
    assert assignments[0].role_name == RoleName.ADMIN.value
    assert {grant.capability for grant in grants} == set(CapabilityKey)
    assert len(grants) == len(CapabilityKey)

    with session_factory() as session:
        repeated = assign_role(
            session,
            actor_id=target_actor.id,
            role_name=RoleName.ADMIN,
            granted_by_actor_id=admin_actor.id,
        )
        session.commit()

    assert repeated.id == assignment.id
    with session_factory() as session:
        repeated_grants = list_capabilities_for_actor(session, target_actor.id)
    assert len(repeated_grants) == len(CapabilityKey)

    with session_factory() as session:
        revoked = revoke_role_assignment(session, assignment.id)
        session.commit()

    assert revoked is not None
    assert revoked.revoked_at is not None
    with session_factory() as session:
        assert list_role_assignments_for_actor(session, target_actor.id) == []
        assert list_capabilities_for_actor(session, target_actor.id) == []
