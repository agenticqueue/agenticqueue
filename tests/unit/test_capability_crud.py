from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.capabilities import (
    get_capability_grant,
    grant_capability,
    list_capabilities_for_actor,
    revoke_capability_grant,
)
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import ActorModel, CapabilityKey, CapabilityRecord
from agenticqueue_api.models.capability import CapabilityGrantModel, CapabilityModel
from agenticqueue_api.repo import create_actor

TRUNCATE_TABLES = [
    "api_token",
    "capability_grant",
    "edge",
    "artifact",
    "decision",
    "run",
    "packet_version",
    "learning",
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
    session_factory: sessionmaker[Session], payload: ActorModel
) -> ActorModel:
    with session_factory() as session:
        actor = create_actor(session, payload)
        session.commit()
        return actor


def seed_token(
    session_factory: sessionmaker[Session],
    *,
    actor_id: uuid.UUID,
) -> str:
    from agenticqueue_api.auth import issue_api_token

    with session_factory() as session:
        _, raw_token = issue_api_token(
            session,
            actor_id=actor_id,
            scopes=["capabilities:write"],
            expires_at=None,
        )
        session.commit()
        return raw_token


def test_grant_happy_path_creates_capability_grant(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    admin = seed_actor(
        session_factory,
        make_actor_payload(
            handle="cap-admin",
            actor_type="admin",
            display_name="Capability Admin",
        ),
    )
    target = seed_actor(
        session_factory,
        make_actor_payload(
            handle="cap-target",
            actor_type="agent",
            display_name="Capability Target",
        ),
    )
    admin_token = seed_token(session_factory, actor_id=admin.id)
    expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)

    response = client.post(
        "/v1/capabilities/grant",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "actor_id": str(target.id),
            "capability": "read_repo",
            "scope": {"project": "agenticqueue-core"},
            "expires_at": expires_at.isoformat(),
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["actor_id"] == str(target.id)
    assert body["capability"] == "read_repo"
    assert body["scope"] == {"project": "agenticqueue-core"}
    assert body["granted_by_actor_id"] == str(admin.id)
    assert body["revoked_at"] is None

    with session_factory() as session:
        stored = get_capability_grant(session, uuid.UUID(body["id"]))

    assert stored is not None
    assert stored.actor_id == target.id
    assert stored.capability is CapabilityKey.READ_REPO
    assert stored.scope == {"project": "agenticqueue-core"}
    assert stored.expires_at == dt.datetime.fromisoformat(body["expires_at"])


def test_revoke_soft_deletes_but_preserves_grant_row(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    admin = seed_actor(
        session_factory,
        make_actor_payload(
            handle="revoke-admin",
            actor_type="admin",
            display_name="Revoke Admin",
        ),
    )
    target = seed_actor(
        session_factory,
        make_actor_payload(
            handle="revoke-target",
            actor_type="agent",
            display_name="Revoke Target",
        ),
    )
    admin_token = seed_token(session_factory, actor_id=admin.id)
    with session_factory() as session:
        grant = grant_capability(
            session,
            actor_id=target.id,
            capability=CapabilityKey.UPDATE_TASK,
            scope={"workspace": "default"},
            granted_by_actor_id=admin.id,
        )
        session.commit()

    response = client.post(
        "/v1/capabilities/revoke",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"grant_id": str(grant.id)},
    )

    assert response.status_code == 200
    assert response.json()["revoked_at"] is not None

    with session_factory() as session:
        stored = get_capability_grant(session, grant.id)

    assert stored is not None
    assert stored.revoked_at is not None
    assert stored.capability is CapabilityKey.UPDATE_TASK


def test_expired_grant_is_absent_from_actor_capability_list(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    admin = seed_actor(
        session_factory,
        make_actor_payload(
            handle="expired-admin",
            actor_type="admin",
            display_name="Expired Admin",
        ),
    )
    target = seed_actor(
        session_factory,
        make_actor_payload(
            handle="expired-target",
            actor_type="agent",
            display_name="Expired Target",
        ),
    )
    target_token = seed_token(session_factory, actor_id=target.id)
    with session_factory() as session:
        grant_capability(
            session,
            actor_id=target.id,
            capability=CapabilityKey.SEARCH_MEMORY,
            scope={"project": "agenticqueue-core"},
            granted_by_actor_id=admin.id,
            expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1),
        )
        session.commit()

    response = client.get(
        f"/v1/actors/{target.id}/capabilities",
        headers={"Authorization": f"Bearer {target_token}"},
    )

    assert response.status_code == 200
    assert response.json()["capabilities"] == []


def test_actor_capability_list_returns_only_active_grants(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    admin = seed_actor(
        session_factory,
        make_actor_payload(
            handle="list-admin",
            actor_type="admin",
            display_name="List Admin",
        ),
    )
    target = seed_actor(
        session_factory,
        make_actor_payload(
            handle="list-target",
            actor_type="agent",
            display_name="List Target",
        ),
    )
    target_token = seed_token(session_factory, actor_id=target.id)
    with session_factory() as session:
        active_grant = grant_capability(
            session,
            actor_id=target.id,
            capability=CapabilityKey.CREATE_ARTIFACT,
            scope={"task_type": "coding-task"},
            granted_by_actor_id=admin.id,
        )
        expired_grant = grant_capability(
            session,
            actor_id=target.id,
            capability=CapabilityKey.RUN_TESTS,
            scope={"repo": "agenticqueue"},
            granted_by_actor_id=admin.id,
            expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5),
        )
        revoked_grant = grant_capability(
            session,
            actor_id=target.id,
            capability=CapabilityKey.WRITE_LEARNING,
            scope={"scope": "project"},
            granted_by_actor_id=admin.id,
        )
        revoke_capability_grant(session, revoked_grant.id)
        session.commit()

    response = client.get(
        f"/v1/actors/{target.id}/capabilities",
        headers={"Authorization": f"Bearer {target_token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["actor"]["id"] == str(target.id)
    assert [grant["id"] for grant in body["capabilities"]] == [str(active_grant.id)]
    assert [grant["capability"] for grant in body["capabilities"]] == [
        "create_artifact"
    ]
    assert str(expired_grant.id) not in json.dumps(body)
    assert str(revoked_grant.id) not in json.dumps(body)


def test_unauthenticated_grant_returns_401(client: TestClient) -> None:
    response = client.post(
        "/v1/capabilities/grant",
        json={
            "actor_id": str(uuid.uuid4()),
            "capability": "read_repo",
            "scope": {},
        },
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {"detail": "Missing Authorization header"}


def test_unauthenticated_revoke_returns_401(client: TestClient) -> None:
    response = client.post(
        "/v1/capabilities/revoke",
        json={"grant_id": str(uuid.uuid4())},
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {"detail": "Missing Authorization header"}


def test_capability_helpers_cover_missing_and_include_inactive(
    session_factory: sessionmaker[Session],
) -> None:
    actor = seed_actor(
        session_factory,
        make_actor_payload(
            handle="helper-target",
            actor_type="agent",
            display_name="Helper Target",
        ),
    )
    admin = seed_actor(
        session_factory,
        make_actor_payload(
            handle="helper-admin",
            actor_type="admin",
            display_name="Helper Admin",
        ),
    )
    missing_grant_id = uuid.uuid4()

    with session_factory() as session:
        active = grant_capability(
            session,
            actor_id=actor.id,
            capability=CapabilityKey.READ_LEARNINGS,
            granted_by_actor_id=admin.id,
        )
        expired = grant_capability(
            session,
            actor_id=actor.id,
            capability=CapabilityKey.SEARCH_MEMORY,
            granted_by_actor_id=admin.id,
            expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1),
        )
        revoked = grant_capability(
            session,
            actor_id=actor.id,
            capability=CapabilityKey.WRITE_BRANCH,
            granted_by_actor_id=admin.id,
        )
        revoke_capability_grant(session, revoked.id)

        assert get_capability_grant(session, missing_grant_id) is None
        assert revoke_capability_grant(session, missing_grant_id) is None
        assert [grant.id for grant in list_capabilities_for_actor(session, actor.id)] == [
            active.id
        ]
        assert {
            grant.id
            for grant in list_capabilities_for_actor(
                session,
                actor.id,
                include_inactive=True,
            )
        } == {
            active.id,
            expired.id,
            revoked.id,
        }
        session.execute(
            sa.delete(CapabilityRecord).where(
                CapabilityRecord.key == CapabilityKey.PROMOTE_LEARNING
            )
        )
        with pytest.raises(ValueError, match="Unknown capability"):
            grant_capability(
                session,
                actor_id=actor.id,
                capability=CapabilityKey.PROMOTE_LEARNING,
                granted_by_actor_id=admin.id,
            )


def test_capability_models_validate_description_scope_and_active_state() -> None:
    capability = CapabilityModel.model_validate(
        {
            "id": str(uuid.uuid4()),
            "key": "admin",
            "description": "  Administrative access  ",
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )
    assert capability.description == "Administrative access"

    with pytest.raises(ValueError, match="description must not be empty"):
        CapabilityModel.model_validate(
            {
                "id": str(uuid.uuid4()),
                "key": "admin",
                "description": "   ",
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
            }
        )

    active_grant = CapabilityGrantModel.model_validate(
        {
            "id": str(uuid.uuid4()),
            "actor_id": str(uuid.uuid4()),
            "capability_id": str(uuid.uuid4()),
            "capability": "admin",
            "scope": None,
            "granted_by_actor_id": None,
            "expires_at": None,
            "revoked_at": None,
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )
    assert active_grant.scope == {}
    assert active_grant.is_active is True

    expired_grant = CapabilityGrantModel.model_validate(
        {
            "id": str(uuid.uuid4()),
            "actor_id": str(uuid.uuid4()),
            "capability_id": str(uuid.uuid4()),
            "capability": "admin",
            "scope": {},
            "granted_by_actor_id": None,
            "expires_at": "2026-04-19T00:00:00+00:00",
            "revoked_at": None,
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )
    assert expired_grant.is_active is False

    revoked_grant = CapabilityGrantModel.model_validate(
        {
            "id": str(uuid.uuid4()),
            "actor_id": str(uuid.uuid4()),
            "capability_id": str(uuid.uuid4()),
            "capability": "admin",
            "scope": {},
            "granted_by_actor_id": None,
            "expires_at": None,
            "revoked_at": "2026-04-20T00:00:00+00:00",
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )
    assert revoked_grant.is_active is False

    with pytest.raises(ValueError, match="scope must be an object"):
        CapabilityGrantModel.model_validate(
            {
                "id": str(uuid.uuid4()),
                "actor_id": str(uuid.uuid4()),
                "capability_id": str(uuid.uuid4()),
                "capability": "admin",
                "scope": "global",
                "granted_by_actor_id": None,
                "expires_at": None,
                "revoked_at": None,
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
            }
        )
