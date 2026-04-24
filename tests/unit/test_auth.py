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
from agenticqueue_api.auth import (
    AuthenticationError,
    _hash_token_secret,
    _token_prefix_from_hash,
    authenticate_api_token,
    extract_bearer_token,
    get_api_token,
    issue_api_token,
    revoke_api_token,
)
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import ActorModel, CapabilityKey, CapabilityRecord
from agenticqueue_api.repo import create_actor

TRUNCATE_TABLES = [
    "api_token",
    "capability_grant",
    "idempotency_key",
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
    scopes: list[str],
    expires_at: dt.datetime | None = None,
) -> tuple[str, uuid.UUID]:
    with session_factory() as session:
        token, raw_token = issue_api_token(
            session,
            actor_id=actor_id,
            scopes=scopes,
            expires_at=expires_at,
        )
        session.commit()
        return raw_token, token.id


def test_valid_bearer_returns_actor_context_and_token_list(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    seed_actor(
        session_factory,
        make_actor_payload(
            handle="user-one",
            actor_type="agent",
            display_name="User One",
        ),
    )
    user_id = actor_id_for("user-one")
    raw_token, _ = seed_token(session_factory, actor_id=user_id, scopes=["read:tokens"])

    response = client.get(
        "/v1/auth/tokens",
        headers={"Authorization": f"Bearer {raw_token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["actor"] == {
        "id": str(user_id),
        "handle": "user-one",
        "actor_type": "agent",
        "display_name": "User One",
    }
    assert body["tokens"][0]["actor_id"] == str(user_id)
    assert body["tokens"][0]["token_prefix"].startswith("aq__")


def test_expired_token_returns_401(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    seed_actor(
        session_factory,
        make_actor_payload(
            handle="expired-user",
            actor_type="agent",
            display_name="Expired User",
        ),
    )
    user_id = actor_id_for("expired-user")
    raw_token, _ = seed_token(
        session_factory,
        actor_id=user_id,
        scopes=["read:tokens"],
        expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1),
    )

    response = client.get(
        "/v1/auth/tokens",
        headers={"Authorization": f"Bearer {raw_token}"},
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    body = response.json()
    assert body["error_code"] == "auth_failed"
    assert body["message"] == "Invalid bearer token"
    assert body["details"] is None


def test_revoked_token_returns_401(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    seed_actor(
        session_factory,
        make_actor_payload(
            handle="revoked-user",
            actor_type="agent",
            display_name="Revoked User",
        ),
    )
    user_id = actor_id_for("revoked-user")
    raw_token, token_id = seed_token(
        session_factory, actor_id=user_id, scopes=["read:tokens"]
    )
    with session_factory() as session:
        revoke_api_token(session, token_id)
        session.commit()

    response = client.get(
        "/v1/auth/tokens",
        headers={"Authorization": f"Bearer {raw_token}"},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error_code"] == "auth_failed"
    assert body["message"] == "Invalid bearer token"
    assert body["details"] is None


def test_malformed_token_returns_401(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    seed_actor(
        session_factory,
        make_actor_payload(
            handle="malformed-user",
            actor_type="agent",
            display_name="Malformed User",
        ),
    )

    response = client.get(
        "/v1/auth/tokens",
        headers={"Authorization": "Token not-a-valid-agenticqueue-token"},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error_code"] == "auth_failed"
    assert body["message"] == "Invalid bearer token"
    assert body["details"] is None


def test_missing_authorization_header_returns_401(client: TestClient) -> None:
    response = client.get("/v1/auth/tokens")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    body = response.json()
    assert body["error_code"] == "auth_failed"
    assert body["message"] == "Missing Authorization header"
    assert body["details"] is None


def test_provision_endpoint_creates_token_and_returns_raw_value_once(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    seed_actor(
        session_factory,
        make_actor_payload(
            handle="admin-one",
            actor_type="admin",
            display_name="Admin One",
        ),
    )
    admin_id = actor_id_for("admin-one")
    seed_actor(
        session_factory,
        make_actor_payload(
            handle="target-user",
            actor_type="agent",
            display_name="Target User",
        ),
    )
    user_id = actor_id_for("target-user")
    admin_token, _ = seed_token(
        session_factory, actor_id=admin_id, scopes=["admin:tokens"]
    )

    response = client.post(
        "/v1/auth/tokens",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
        json={
            "actor_id": str(user_id),
            "scopes": ["task:read", "task:read", "task:write"],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token"].startswith("aq__")
    assert body["api_token"]["actor_id"] == str(user_id)
    assert body["api_token"]["scopes"] == ["task:read", "task:write"]
    assert "raw_token" not in body["api_token"]


def test_revoke_endpoint_marks_token_and_blocks_future_use(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    seed_actor(
        session_factory,
        make_actor_payload(
            handle="self-revoke-user",
            actor_type="agent",
            display_name="Self Revoke User",
        ),
    )
    user_id = actor_id_for("self-revoke-user")
    raw_token, token_id = seed_token(
        session_factory, actor_id=user_id, scopes=["read:tokens"]
    )

    revoke_response = client.post(
        f"/v1/auth/tokens/{token_id}/revoke",
        headers={
            "Authorization": f"Bearer {raw_token}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )

    assert revoke_response.status_code == 200
    assert revoke_response.json()["revoked_at"] is not None

    list_response = client.get(
        "/v1/auth/tokens",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert list_response.status_code == 401
    body = list_response.json()
    assert body["error_code"] == "auth_failed"
    assert body["message"] == "Invalid bearer token"
    assert body["details"] is None


def test_non_admin_cannot_revoke_another_actors_token(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    seed_actor(
        session_factory,
        make_actor_payload(
            handle="revoke-owner",
            actor_type="agent",
            display_name="Revoke Owner",
        ),
    )
    owner_id = actor_id_for("revoke-owner")
    owner_token, _ = seed_token(
        session_factory, actor_id=owner_id, scopes=["read:tokens"]
    )

    seed_actor(
        session_factory,
        make_actor_payload(
            handle="revoke-target",
            actor_type="agent",
            display_name="Revoke Target",
        ),
    )
    target_id = actor_id_for("revoke-target")
    _, target_token_id = seed_token(
        session_factory, actor_id=target_id, scopes=["read:tokens"]
    )

    response = client.post(
        f"/v1/auth/tokens/{target_token_id}/revoke",
        headers={
            "Authorization": f"Bearer {owner_token}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 404
    assert response.json()["message"] == "Token not found"


def test_list_endpoint_returns_only_requesting_actor_tokens_without_raw_values(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    seed_actor(
        session_factory,
        make_actor_payload(
            handle="list-user",
            actor_type="agent",
            display_name="List User",
        ),
    )
    user_id = actor_id_for("list-user")
    seed_actor(
        session_factory,
        make_actor_payload(
            handle="other-user",
            actor_type="agent",
            display_name="Other User",
        ),
    )
    other_user_id = actor_id_for("other-user")
    user_token, _ = seed_token(
        session_factory, actor_id=user_id, scopes=["read:tokens"]
    )
    second_user_token, _ = seed_token(
        session_factory, actor_id=user_id, scopes=["task:write"]
    )
    seed_token(session_factory, actor_id=other_user_id, scopes=["read:tokens"])

    response = client.get(
        "/v1/auth/tokens",
        headers={"Authorization": f"Bearer {user_token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["tokens"]) == 2
    assert {token["actor_id"] for token in body["tokens"]} == {str(user_id)}
    serialized_body = json.dumps(body)
    assert user_token not in serialized_body
    assert second_user_token not in serialized_body


def test_auth_helpers_handle_missing_rows_and_invalid_token_shapes(
    session_factory: sessionmaker[Session],
) -> None:
    missing_token_id = uuid.uuid4()
    with pytest.raises(AuthenticationError):
        extract_bearer_token("Bearer   ")

    with session_factory() as session:
        assert get_api_token(session, missing_token_id) is None
        assert revoke_api_token(session, missing_token_id) is None
        assert authenticate_api_token(session, "not-prefixed") is None
        assert authenticate_api_token(session, "aq__missing-separator") is None


def test_authenticate_token_handles_prefix_mismatch_and_unknown_hash(
    session_factory: sessionmaker[Session],
) -> None:
    actor = seed_actor(
        session_factory,
        make_actor_payload(
            handle="prefix-user",
            actor_type="agent",
            display_name="Prefix User",
        ),
    )
    raw_token, _ = seed_token(
        session_factory, actor_id=actor.id, scopes=["read:tokens"]
    )

    tampered_prefix_token = raw_token.replace(
        raw_token[4], "0" if raw_token[4] != "0" else "1", 1
    )
    assert tampered_prefix_token != raw_token

    unknown_secret = "f" * 64
    unknown_hash = _hash_token_secret(unknown_secret)
    unknown_token = f"aq__{_token_prefix_from_hash(unknown_hash)}_{unknown_secret}"

    with session_factory() as session:
        assert (
            authenticate_api_token(session, raw_token, now=dt.datetime.now(dt.UTC))
            is not None
        )
        assert authenticate_api_token(session, tampered_prefix_token) is None
        assert authenticate_api_token(session, unknown_token) is None
