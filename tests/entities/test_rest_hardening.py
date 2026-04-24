from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.models import AuditLogRecord

from .helpers import auth_headers, seed_actor, seed_token


def _seed_workspace(
    client: TestClient,
    token: str,
    *,
    slug: str,
    name: str,
    description: str | None = None,
) -> str:
    response = client.post(
        "/v1/workspaces",
        headers=auth_headers(token),
        json={
            "id": str(uuid.uuid4()),
            "slug": slug,
            "name": name,
            "description": description or f"{name} description",
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_workspace_list_supports_limit_and_cursor_headers(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    actor = seed_actor(
        session_factory,
        handle="workspace-pager",
        actor_type="admin",
        display_name="Workspace Pager",
    )
    token = seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=["workspace:read", "workspace:write"],
    )
    batch_description = "pagination-batch"
    created_ids = [
        _seed_workspace(
            client,
            token,
            slug=f"workspace-{index}",
            name=f"Workspace {index}",
            description=batch_description,
        )
        for index in range(3)
    ]
    expected_ids = sorted(created_ids)

    first_page = client.get(
        "/v1/workspaces",
        headers=auth_headers(token),
        params={"description": batch_description, "limit": "1"},
    )

    assert first_page.status_code == 200
    assert first_page.headers["X-List-Limit"] == "1"
    assert "X-Next-Cursor" in first_page.headers
    assert [item["id"] for item in first_page.json()] == [expected_ids[0]]

    second_page = client.get(
        "/v1/workspaces",
        headers=auth_headers(token),
        params={
            "description": batch_description,
            "limit": "1",
            "cursor": first_page.headers["X-Next-Cursor"],
        },
    )

    assert second_page.status_code == 200
    assert second_page.headers["X-List-Limit"] == "1"
    assert [item["id"] for item in second_page.json()] == [expected_ids[1]]
    assert second_page.headers["X-Next-Cursor"] != first_page.headers["X-Next-Cursor"]


def test_request_id_is_echoed_and_written_to_audit_log(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    actor = seed_actor(
        session_factory,
        handle="request-id-admin",
        actor_type="admin",
        display_name="Request ID Admin",
    )
    token = seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=["workspace:read", "workspace:write"],
    )
    request_id = "req-hardening-001"
    response = client.post(
        "/v1/workspaces",
        headers={
            **auth_headers(token),
            "X-Request-Id": request_id,
        },
        json={
            "id": str(uuid.uuid4()),
            "slug": "workspace-request-id",
            "name": "Workspace Request ID",
            "description": "Tracks request IDs",
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        },
    )

    assert response.status_code == 201
    assert response.headers["X-Request-Id"] == request_id
    workspace_id = uuid.UUID(response.json()["id"])

    with session_factory() as session:
        trace_id = session.scalar(
            sa.select(AuditLogRecord.trace_id)
            .where(
                AuditLogRecord.entity_type == "workspace",
                AuditLogRecord.entity_id == workspace_id,
                AuditLogRecord.action == "CREATE",
            )
            .order_by(AuditLogRecord.created_at.desc(), AuditLogRecord.id.desc())
        )

    assert trace_id == request_id


def test_rate_limit_returns_structured_429(
    session_factory: sessionmaker[Session],
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENTICQUEUE_RATE_LIMIT_RPS", "1")
    monkeypatch.setenv("AGENTICQUEUE_RATE_LIMIT_BURST", "1")
    actor = seed_actor(
        session_factory,
        handle="rate-limit-admin",
        actor_type="admin",
        display_name="Rate Limit Admin",
    )
    token = seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=["workspace:read"],
    )

    app = create_app(session_factory=session_factory)
    with TestClient(app) as test_client:
        first = test_client.get("/v1/workspaces", headers=auth_headers(token))
        second = test_client.get("/v1/workspaces", headers=auth_headers(token))

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "1"
    assert second.headers["X-Request-Id"]
    assert second.json()["error_code"] == "rate_limited"
    assert second.json()["message"] == "Rate limit exceeded"


def test_authenticated_read_reuses_one_request_db_session(
    session_factory: sessionmaker[Session],
) -> None:
    class CountingSessionFactory:
        def __init__(self, delegate: sessionmaker[Session]) -> None:
            self._delegate = delegate
            self.call_count = 0

        def __call__(self) -> Session:
            self.call_count += 1
            return self._delegate()

    actor = seed_actor(
        session_factory,
        handle="workspace-session-admin",
        actor_type="admin",
        display_name="Workspace Session Admin",
    )
    token = seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=["workspace:read"],
    )
    counting_factory = CountingSessionFactory(session_factory)

    app = create_app(session_factory=counting_factory)  # type: ignore[arg-type]
    with TestClient(app) as test_client:
        response = test_client.get("/v1/workspaces", headers=auth_headers(token))

    assert response.status_code == 200
    assert counting_factory.call_count == 1
