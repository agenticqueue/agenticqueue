from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
from collections.abc import Iterator
from typing import Any

from fastapi.testclient import TestClient
from fastmcp import Client as FastMCPClient
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import (
    ActorModel,
    AuditLogRecord,
    CapabilityKey,
    CapabilityRecord,
)
from agenticqueue_api.repo import create_actor
from agenticqueue_cli.client import CliState, OutputFormat
from agenticqueue_cli.main import app as cli_app

TRUNCATE_TABLES = [
    "api_token",
    "capability_grant",
    "idempotency_key",
    "edge",
    "artifact",
    "decision",
    "run",
    "packet_version",
    "learning_drafts",
    "learning",
    "task",
    "project",
    "policy",
    "capability",
    "audit_log",
    "workspace",
    "actor",
]


def _actor_payload(*, handle: str, actor_type: str) -> ActorModel:
    return ActorModel.model_validate(
        {
            "id": str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"https://agenticqueue.ai/tests/audit/{handle}",
                )
            ),
            "handle": handle,
            "actor_type": actor_type,
            "display_name": handle.replace("-", " ").title(),
            "auth_subject": f"{handle}-subject",
            "is_active": True,
            "created_at": "2026-04-21T00:00:00+00:00",
            "updated_at": "2026-04-21T00:00:00+00:00",
        }
    )


def _truncate_all_tables(engine: Engine) -> None:
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


def _mcp_call(server, tool_name: str, arguments: dict[str, object]) -> dict[str, Any]:
    async def _invoke() -> dict[str, Any]:
        async with FastMCPClient(server) as client:
            result = await client.call_tool(tool_name, arguments)
            return result.data

    return asyncio.run(_invoke())


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> None:
    _truncate_all_tables(engine)


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(create_app(session_factory=session_factory)) as test_client:
        yield test_client


def _seed_actor(
    session_factory: sessionmaker[Session],
    *,
    handle: str,
    actor_type: str,
) -> ActorModel:
    with session_factory() as session:
        actor = create_actor(
            session,
            _actor_payload(handle=handle, actor_type=actor_type),
        )
        session.commit()
        return actor


def _issue_token(
    session_factory: sessionmaker[Session],
    *,
    actor_id: uuid.UUID,
    scopes: tuple[str, ...],
) -> str:
    with session_factory() as session:
        _, token = issue_api_token(
            session,
            actor_id=actor_id,
            scopes=list(scopes),
            expires_at=None,
        )
        session.commit()
        return token


def _insert_audit_row(
    session: Session,
    *,
    actor_id: uuid.UUID,
    entity_type: str,
    action: str,
    created_at: dt.datetime,
    entity_id: uuid.UUID | None = None,
) -> None:
    session.execute(
        sa.insert(AuditLogRecord).values(
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=entity_id or uuid.uuid4(),
            action=action,
            before=None,
            after={"action": action, "entity_type": entity_type},
            trace_id=f"trace-{action.lower()}-{created_at.isoformat()}",
            redaction=None,
            created_at=created_at,
        )
    )


def _seed_audit_rows(
    session_factory: sessionmaker[Session],
) -> tuple[ActorModel, ActorModel, ActorModel]:
    admin = _seed_actor(session_factory, handle="audit-admin", actor_type="admin")
    actor_one = _seed_actor(
        session_factory, handle="audit-agent-one", actor_type="agent"
    )
    actor_two = _seed_actor(
        session_factory, handle="audit-agent-two", actor_type="agent"
    )

    with session_factory() as session:
        _insert_audit_row(
            session,
            actor_id=actor_one.id,
            entity_type="task",
            action="CREATE",
            created_at=dt.datetime(2026, 4, 21, 12, 0, tzinfo=dt.UTC),
        )
        _insert_audit_row(
            session,
            actor_id=actor_one.id,
            entity_type="task",
            action="UPDATE",
            created_at=dt.datetime(2026, 4, 21, 12, 5, tzinfo=dt.UTC),
        )
        _insert_audit_row(
            session,
            actor_id=actor_one.id,
            entity_type="workspace",
            action="CREATE",
            created_at=dt.datetime(2026, 4, 21, 12, 10, tzinfo=dt.UTC),
        )
        _insert_audit_row(
            session,
            actor_id=actor_two.id,
            entity_type="task",
            action="CREATE",
            created_at=dt.datetime(2026, 4, 21, 12, 15, tzinfo=dt.UTC),
        )
        _insert_audit_row(
            session,
            actor_id=actor_one.id,
            entity_type="task",
            action="DELETE",
            created_at=dt.datetime(2026, 4, 21, 12, 20, tzinfo=dt.UTC),
        )
        session.commit()

    return admin, actor_one, actor_two


def test_get_audit_filters_by_actor_and_entity_type_with_cursor_pagination(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    admin, actor_one, _ = _seed_audit_rows(session_factory)
    token = _issue_token(
        session_factory,
        actor_id=admin.id,
        scopes=("audit:read",),
    )

    response = client.get(
        "/v1/audit",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "actor_id": str(actor_one.id),
            "entity_type": "task",
            "limit": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["action"] for item in payload["items"]] == ["DELETE", "UPDATE"]
    assert payload["next_cursor"] is not None
    assert response.headers["X-List-Limit"] == "2"
    assert response.headers["X-Next-Cursor"] == payload["next_cursor"]

    second_page = client.get(
        "/v1/audit",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "actor_id": str(actor_one.id),
            "entity_type": "task",
            "limit": 2,
            "cursor": payload["next_cursor"],
        },
    )

    assert second_page.status_code == 200
    second_payload = second_page.json()
    assert [item["action"] for item in second_payload["items"]] == ["CREATE"]
    assert second_payload["next_cursor"] is None


def test_get_audit_filters_by_time_range(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    admin, _, _ = _seed_audit_rows(session_factory)
    token = _issue_token(
        session_factory,
        actor_id=admin.id,
        scopes=("audit:read",),
    )
    with session_factory() as session:
        task_rows = session.scalars(
            sa.select(AuditLogRecord)
            .where(AuditLogRecord.entity_type == "task")
            .order_by(AuditLogRecord.created_at.desc(), AuditLogRecord.id.desc())
        ).all()

    since = task_rows[2].created_at
    until = task_rows[1].created_at
    expected_actions = [
        row.action for row in task_rows if since <= row.created_at <= until
    ]

    response = client.get(
        "/v1/audit",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "entity_type": "task",
            "since": since.isoformat(),
            "until": until.isoformat(),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["action"] for item in payload["items"]] == expected_actions


def test_query_audit_log_mcp_matches_rest_shape_with_cursor(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    admin, actor_one, _ = _seed_audit_rows(session_factory)
    token = _issue_token(
        session_factory,
        actor_id=admin.id,
        scopes=("audit:read",),
    )
    app = create_app(session_factory=session_factory)

    rest_response = client.get(
        "/v1/audit",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "actor_id": str(actor_one.id),
            "entity_type": "task",
            "limit": 2,
        },
    )
    mcp_response = _mcp_call(
        app.state.mcp_server,
        "query_audit_log",
        {
            "token": token,
            "actor_id": str(actor_one.id),
            "entity_type": "task",
            "limit": 2,
        },
    )

    assert rest_response.status_code == 200
    assert mcp_response == rest_response.json()


def test_get_audit_requires_valid_token(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _seed_audit_rows(session_factory)

    response = client.get("/v1/audit")

    assert response.status_code == 401


def test_cli_audit_type_flag_maps_to_entity_type_and_prints_rows() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def request_json(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return {
                "items": [
                    {
                        "id": "row-1",
                        "entity_type": "task",
                        "action": "CREATE",
                        "created_at": "2026-04-21T12:00:00+00:00",
                    }
                ],
                "next_cursor": None,
            }

    runner = CliRunner()
    fake_client = FakeClient()
    state = CliState(
        server="http://testserver",
        token="audit-token",
        output=OutputFormat.JSON,
        verbose=False,
        client=fake_client,
    )

    result = runner.invoke(cli_app, ["audit", "--type", "task"], obj=state)

    assert result.exit_code == 0, result.stderr
    assert fake_client.calls == [
        {
            "method": "GET",
            "path": "/v1/audit",
            "params": {"entity_type": "task"},
        }
    ]
    payload = json.loads(result.stdout)
    assert payload["items"][0]["entity_type"] == "task"
