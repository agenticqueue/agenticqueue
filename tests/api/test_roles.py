from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import ActorModel, CapabilityKey, CapabilityRecord
from agenticqueue_api.models.role import RoleName, STANDARD_ROLE_DEFINITIONS
from agenticqueue_api.repo import create_actor

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


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app(session_factory=session_factory)
    with TestClient(app) as test_client:
        yield test_client


def seed_actor(
    session_factory: sessionmaker[Session],
    payload: ActorModel,
) -> ActorModel:
    with session_factory() as session:
        actor = create_actor(session, payload)
        session.commit()
        return actor


def seed_token(
    session_factory: sessionmaker[Session],
    *,
    actor_id: uuid.UUID,
    scopes: list[str],
) -> str:
    with session_factory() as session:
        _, raw_token = issue_api_token(
            session,
            actor_id=actor_id,
            scopes=scopes,
            expires_at=None,
        )
        session.commit()
        return raw_token


def auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": str(uuid.uuid4()),
    }


def test_admin_can_list_roles_and_manage_role_assignments_over_rest(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    admin_actor = seed_actor(
        session_factory,
        make_actor_payload(
            handle="roles-admin",
            actor_type="admin",
            display_name="Roles Admin",
        ),
    )
    target_actor = seed_actor(
        session_factory,
        make_actor_payload(
            handle="roles-target",
            actor_type="agent",
            display_name="Roles Target",
        ),
    )
    admin_token = seed_token(session_factory, actor_id=admin_actor.id, scopes=["admin"])
    target_token = seed_token(
        session_factory, actor_id=target_actor.id, scopes=["self"]
    )

    list_response = client.get(
        "/v1/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert list_response.status_code == 200
    assert {role["name"] for role in list_response.json()["roles"]} == {
        role_name.value for role_name in RoleName
    }

    assign_response = client.post(
        "/v1/roles/assign",
        headers=auth_headers(admin_token),
        json={
            "actor_id": str(target_actor.id),
            "role_name": RoleName.CONTRIBUTOR.value,
        },
    )
    assert assign_response.status_code == 201
    assigned = assign_response.json()
    assert assigned["actor_id"] == str(target_actor.id)
    assert assigned["role_name"] == RoleName.CONTRIBUTOR.value
    assert set(assigned["capabilities"]) == {
        capability.value
        for capability in STANDARD_ROLE_DEFINITIONS[RoleName.CONTRIBUTOR][
            "capabilities"
        ]
    }

    actor_roles_response = client.get(
        f"/v1/actors/{target_actor.id}/roles",
        headers={"Authorization": f"Bearer {target_token}"},
    )
    assert actor_roles_response.status_code == 200
    actor_roles = actor_roles_response.json()["roles"]
    assert len(actor_roles) == 1
    assert actor_roles[0]["role_name"] == RoleName.CONTRIBUTOR.value

    capability_response = client.get(
        f"/v1/actors/{target_actor.id}/capabilities",
        headers={"Authorization": f"Bearer {target_token}"},
    )
    assert capability_response.status_code == 200
    assert {
        item["capability"] for item in capability_response.json()["capabilities"]
    } == {
        capability.value
        for capability in STANDARD_ROLE_DEFINITIONS[RoleName.CONTRIBUTOR][
            "capabilities"
        ]
    }

    revoke_response = client.post(
        "/v1/roles/revoke",
        headers=auth_headers(admin_token),
        json={"assignment_id": assigned["id"]},
    )
    assert revoke_response.status_code == 200
    assert revoke_response.json()["revoked_at"] is not None

    actor_roles_after = client.get(
        f"/v1/actors/{target_actor.id}/roles",
        headers={"Authorization": f"Bearer {target_token}"},
    )
    assert actor_roles_after.status_code == 200
    assert actor_roles_after.json()["roles"] == []

    capability_after = client.get(
        f"/v1/actors/{target_actor.id}/capabilities",
        headers={"Authorization": f"Bearer {target_token}"},
    )
    assert capability_after.status_code == 200
    assert capability_after.json()["capabilities"] == []


def test_non_admin_cannot_assign_roles_over_rest(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    actor = seed_actor(
        session_factory,
        make_actor_payload(
            handle="roles-agent",
            actor_type="agent",
            display_name="Roles Agent",
        ),
    )
    target_actor = seed_actor(
        session_factory,
        make_actor_payload(
            handle="roles-target-guard",
            actor_type="agent",
            display_name="Roles Target Guard",
        ),
    )
    actor_token = seed_token(session_factory, actor_id=actor.id, scopes=["agent"])

    response = client.post(
        "/v1/roles/assign",
        headers=auth_headers(actor_token),
        json={
            "actor_id": str(target_actor.id),
            "role_name": RoleName.REVIEWER.value,
        },
    )

    assert response.status_code == 403
    assert response.json()["message"] == "Admin actor required"
