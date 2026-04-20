from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.capabilities import grant_capability, require_capability
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import (
    ActorModel,
    AuditLogRecord,
    CapabilityGrantModel,
    CapabilityKey,
    CapabilityRecord,
    ProjectModel,
    TaskModel,
    WorkspaceModel,
)
from agenticqueue_api.repo import (
    create_actor,
    create_project,
    create_task,
    create_workspace,
)

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


def example_contract() -> dict[str, object]:
    path = (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "tasks"
        / "coding"
        / "01-add-endpoint.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def auth_headers(token: str, trace_id: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": str(uuid.uuid4()),
    }
    if trace_id is not None:
        headers["X-Trace-Id"] = trace_id
    return headers


def seed_actor(
    session_factory: sessionmaker[Session],
    *,
    handle: str,
    actor_type: str = "agent",
) -> ActorModel:
    with session_factory() as session:
        actor = create_actor(
            session,
            make_actor_payload(
                handle=handle,
                actor_type=actor_type,
                display_name=handle.replace("-", " ").title(),
            ),
        )
        session.commit()
        return actor


def seed_workspace_project_task(
    session_factory: sessionmaker[Session],
    *,
    suffix: str,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    contract = example_contract()
    with session_factory() as session:
        workspace = create_workspace(
            session,
            WorkspaceModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "slug": f"workspace-{suffix}",
                    "name": f"Workspace {suffix}",
                    "description": "Capability enforcement workspace",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        project = create_project(
            session,
            ProjectModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "workspace_id": str(workspace.id),
                    "slug": f"project-{suffix}",
                    "name": f"Project {suffix}",
                    "description": "Capability enforcement project",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        task = create_task(
            session,
            TaskModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": f"Task {suffix}",
                    "state": "queued",
                    "description": "Capability enforcement task",
                    "contract": contract,
                    "definition_of_done": contract["dod_checklist"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        session.commit()
        return workspace.id, project.id, task.id


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


def seed_capability_grant(
    session_factory: sessionmaker[Session],
    *,
    actor_id: uuid.UUID,
    capability: CapabilityKey,
    scope: dict[str, object],
) -> CapabilityGrantModel:
    with session_factory() as session:
        grant = grant_capability(
            session,
            actor_id=actor_id,
            capability=capability,
            scope=scope,
        )
        session.commit()
        return grant


ALL_STANDARD_CAPABILITIES = tuple(CapabilityKey)
REQUIRED_PROJECT_SCOPE = {"project_id": "matrix-project"}
MISMATCHED_PROJECT_SCOPE = {"project_id": "other-project"}


def make_capability_request(actor: ActorModel) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(actor=actor))


def make_project_scoped_dependency(capability: CapabilityKey):
    return require_capability(
        capability,
        lambda request, session, payload, entity_id: REQUIRED_PROJECT_SCOPE,
        entity_type="task",
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


def test_task_mutation_with_matching_capability_and_scope_succeeds(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    actor = seed_actor(session_factory, handle="cap-match")
    _, project_id, task_id = seed_workspace_project_task(
        session_factory, suffix="match"
    )
    token = seed_token(session_factory, actor_id=actor.id, scopes=["task:write"])
    seed_capability_grant(
        session_factory,
        actor_id=actor.id,
        capability=CapabilityKey.WRITE_BRANCH,
        scope={"project_id": str(project_id)},
    )

    response = client.patch(
        f"/v1/tasks/{task_id}",
        headers=auth_headers(token),
        json={"title": "Task match updated"},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Task match updated"


def test_task_mutation_without_capability_returns_structured_403(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    actor = seed_actor(session_factory, handle="cap-missing")
    _, project_id, task_id = seed_workspace_project_task(
        session_factory, suffix="missing"
    )
    token = seed_token(session_factory, actor_id=actor.id, scopes=["task:write"])

    response = client.patch(
        f"/v1/tasks/{task_id}",
        headers=auth_headers(token),
        json={"title": "Task missing updated"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "forbidden"
    assert response.json()["details"] == {
        "missing_capability": "write_branch",
        "required_scope": {"project_id": str(project_id)},
    }


def test_task_mutation_with_wrong_scope_returns_structured_403(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    actor = seed_actor(session_factory, handle="cap-wrong-scope")
    _, required_project_id, task_id = seed_workspace_project_task(
        session_factory, suffix="required"
    )
    _, other_project_id, _ = seed_workspace_project_task(
        session_factory, suffix="other"
    )
    token = seed_token(session_factory, actor_id=actor.id, scopes=["task:write"])
    seed_capability_grant(
        session_factory,
        actor_id=actor.id,
        capability=CapabilityKey.WRITE_BRANCH,
        scope={"project_id": str(other_project_id)},
    )

    response = client.patch(
        f"/v1/tasks/{task_id}",
        headers=auth_headers(token),
        json={"title": "Task wrong scope updated"},
    )

    assert response.status_code == 403
    assert response.json()["details"] == {
        "missing_capability": "write_branch",
        "required_scope": {"project_id": str(required_project_id)},
    }


def test_capability_denial_writes_one_audit_log_row(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    actor = seed_actor(session_factory, handle="cap-audit")
    _, project_id, task_id = seed_workspace_project_task(
        session_factory, suffix="audit"
    )
    token = seed_token(session_factory, actor_id=actor.id, scopes=["task:write"])

    response = client.patch(
        f"/v1/tasks/{task_id}",
        headers=auth_headers(token, trace_id="trace-capability-denial"),
        json={"title": "Task audit updated"},
    )

    assert response.status_code == 403

    with session_factory() as session:
        rows = session.scalars(
            sa.select(AuditLogRecord)
            .where(
                AuditLogRecord.entity_type == "task",
                AuditLogRecord.entity_id == task_id,
                AuditLogRecord.action == "CAPABILITY_DENIED",
            )
            .order_by(AuditLogRecord.created_at.asc(), AuditLogRecord.id.asc())
        ).all()

    assert len(rows) == 1
    assert rows[0].actor_id == actor.id
    assert rows[0].trace_id == "trace-capability-denial"
    assert rows[0].before is None
    assert rows[0].after == {
        "missing_capability": "write_branch",
        "required_scope": {"project_id": str(project_id)},
    }


def test_every_crud_mutation_route_has_capability_dependency(
    session_factory: sessionmaker[Session],
) -> None:
    app = create_app(session_factory=session_factory)
    crud_mutation_routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/v1/")
        and route.name.startswith(("create_", "update_", "delete_"))
    ]

    assert crud_mutation_routes
    for route in crud_mutation_routes:
        dependency_names = [
            dependency.call.__name__
            for dependency in route.dependant.dependencies
            if dependency.call is not None
        ]
        assert any(
            name.startswith("require_capability_") for name in dependency_names
        ), f"missing capability dependency on {sorted(route.methods)} {route.path}"


@pytest.mark.parametrize(
    "capability",
    ALL_STANDARD_CAPABILITIES,
    ids=lambda capability: capability.value,
)
def test_each_capability_allows_matching_project_scoped_grants(
    session_factory: sessionmaker[Session],
    capability: CapabilityKey,
) -> None:
    actor = seed_actor(session_factory, handle=f"cap-pass-{capability.value}")
    dependency = make_project_scoped_dependency(capability)
    seed_capability_grant(
        session_factory,
        actor_id=actor.id,
        capability=capability,
        scope=REQUIRED_PROJECT_SCOPE,
    )

    with session_factory() as session:
        assert (
            dependency(
                request=make_capability_request(actor),
                session=session,
                payload=None,
                entity_id=None,
            )
            is None
        )


@pytest.mark.parametrize(
    "capability",
    ALL_STANDARD_CAPABILITIES,
    ids=lambda capability: capability.value,
)
def test_each_capability_denies_missing_grants(
    session_factory: sessionmaker[Session],
    capability: CapabilityKey,
) -> None:
    actor = seed_actor(session_factory, handle=f"cap-deny-{capability.value}")
    dependency = make_project_scoped_dependency(capability)

    with session_factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            dependency(
                request=make_capability_request(actor),
                session=session,
                payload=None,
                entity_id=None,
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {
        "error_code": "forbidden",
        "message": "Capability grant required",
        "details": {
            "missing_capability": capability.value,
            "required_scope": REQUIRED_PROJECT_SCOPE,
        },
    }


@pytest.mark.parametrize(
    "capability",
    ALL_STANDARD_CAPABILITIES,
    ids=lambda capability: capability.value,
)
def test_each_capability_rejects_scope_mismatches(
    session_factory: sessionmaker[Session],
    capability: CapabilityKey,
) -> None:
    actor = seed_actor(session_factory, handle=f"cap-scope-{capability.value}")
    dependency = make_project_scoped_dependency(capability)
    seed_capability_grant(
        session_factory,
        actor_id=actor.id,
        capability=capability,
        scope=MISMATCHED_PROJECT_SCOPE,
    )

    with session_factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            dependency(
                request=make_capability_request(actor),
                session=session,
                payload=None,
                entity_id=None,
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {
        "error_code": "forbidden",
        "message": "Capability grant required",
        "details": {
            "missing_capability": capability.value,
            "required_scope": REQUIRED_PROJECT_SCOPE,
        },
    }


@pytest.mark.parametrize(
    "capability",
    ALL_STANDARD_CAPABILITIES,
    ids=lambda capability: capability.value,
)
def test_each_capability_rejects_revoked_grants(
    session_factory: sessionmaker[Session],
    capability: CapabilityKey,
) -> None:
    from agenticqueue_api.capabilities import revoke_capability_grant

    actor = seed_actor(session_factory, handle=f"cap-revoked-{capability.value}")
    dependency = make_project_scoped_dependency(capability)
    grant = seed_capability_grant(
        session_factory,
        actor_id=actor.id,
        capability=capability,
        scope=REQUIRED_PROJECT_SCOPE,
    )

    with session_factory() as session:
        revoke_capability_grant(session, grant.id)
        session.commit()

    with session_factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            dependency(
                request=make_capability_request(actor),
                session=session,
                payload=None,
                entity_id=None,
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {
        "error_code": "forbidden",
        "message": "Capability grant required",
        "details": {
            "missing_capability": capability.value,
            "required_scope": REQUIRED_PROJECT_SCOPE,
        },
    }


def test_capability_helpers_cover_lookup_revocation_and_unknown_capability(
    session_factory: sessionmaker[Session],
) -> None:
    actor = seed_actor(session_factory, handle="cap-helper-actor")
    admin = seed_actor(
        session_factory,
        handle="cap-helper-admin",
        actor_type="admin",
    )
    missing_grant_id = uuid.uuid4()

    with session_factory() as session:
        active = grant_capability(
            session,
            actor_id=actor.id,
            capability=CapabilityKey.READ_REPO,
            scope={"project_id": "active-project"},
            granted_by_actor_id=admin.id,
        )
        expired = grant_capability(
            session,
            actor_id=actor.id,
            capability=CapabilityKey.SEARCH_MEMORY,
            scope={"project_id": "expired-project"},
            granted_by_actor_id=admin.id,
            expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1),
        )
        revoked = grant_capability(
            session,
            actor_id=actor.id,
            capability=CapabilityKey.WRITE_BRANCH,
            scope={"project_id": "revoked-project"},
            granted_by_actor_id=admin.id,
        )
        session.commit()

    from agenticqueue_api.capabilities import (
        get_capability_grant,
        list_capabilities_for_actor,
        revoke_capability_grant,
    )

    with session_factory() as session:
        stored = get_capability_grant(session, active.id)
        assert stored is not None
        assert stored.id == active.id
        assert get_capability_grant(session, missing_grant_id) is None

        revoked_model = revoke_capability_grant(session, revoked.id)
        assert revoked_model is not None
        assert revoked_model.revoked_at is not None
        assert revoke_capability_grant(session, missing_grant_id) is None

        active_only_ids = {
            grant.id for grant in list_capabilities_for_actor(session, actor.id)
        }
        assert active.id in active_only_ids
        assert expired.id not in active_only_ids
        assert revoked.id not in active_only_ids

        all_ids = {
            grant.id
            for grant in list_capabilities_for_actor(
                session,
                actor.id,
                include_inactive=True,
            )
        }
        assert all_ids == {active.id, expired.id, revoked.id}

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


def test_require_capability_handles_missing_actor_and_admin_bypass(
    session_factory: sessionmaker[Session],
) -> None:
    dependency = require_capability(CapabilityKey.UPDATE_TASK, entity_type="task")
    actor = seed_actor(session_factory, handle="cap-empty-scope")
    admin_actor = make_actor_payload(
        handle="cap-admin-bypass",
        actor_type="admin",
        display_name="Capability Admin Bypass",
    )
    seed_capability_grant(
        session_factory,
        actor_id=actor.id,
        capability=CapabilityKey.UPDATE_TASK,
        scope={},
    )

    with session_factory() as session:
        missing_request = SimpleNamespace(state=SimpleNamespace())
        with pytest.raises(HTTPException) as exc_info:
            dependency(
                request=missing_request,
                session=session,
                payload=None,
                entity_id=None,
            )
        assert exc_info.value.status_code == 401

        admin_request = SimpleNamespace(state=SimpleNamespace(actor=admin_actor))
        assert (
            dependency(
                request=admin_request,
                session=session,
                payload=None,
                entity_id=None,
            )
            is None
        )

        actor_request = SimpleNamespace(state=SimpleNamespace(actor=actor))
        assert (
            dependency(
                request=actor_request,
                session=session,
                payload=None,
                entity_id=None,
            )
            is None
        )


def test_capability_internal_helpers_cover_scope_and_entity_id_branches(
    session_factory: sessionmaker[Session],
) -> None:
    from agenticqueue_api import capabilities as capabilities_module

    actor = seed_actor(session_factory, handle="cap-helper-branches")
    seed_capability_grant(
        session_factory,
        actor_id=actor.id,
        capability=CapabilityKey.READ_REPO,
        scope={"repo": "agenticqueue"},
    )
    seed_capability_grant(
        session_factory,
        actor_id=actor.id,
        capability=CapabilityKey.UPDATE_TASK,
        scope={},
    )
    dependency = require_capability(
        CapabilityKey.UPDATE_TASK,
        lambda request, session, payload, entity_id: {"project_id": "project-1"},
        entity_type="task",
    )
    payload_id = uuid.uuid4()

    assert capabilities_module._grant_covers_scope({}, {"project_id": "project-1"})
    assert capabilities_module._coerce_entity_id(None) is None
    assert capabilities_module._coerce_entity_id(payload_id) == payload_id
    assert capabilities_module._coerce_entity_id(str(payload_id)) == payload_id
    assert capabilities_module._coerce_entity_id("not-a-uuid") is None

    with session_factory() as session:
        request = SimpleNamespace(state=SimpleNamespace(actor=actor))
        assert (
            dependency(
                request=request,
                session=session,
                payload={"id": str(payload_id)},
                entity_id=None,
            )
            is None
        )

    denied_actor = seed_actor(session_factory, handle="cap-helper-denied")
    with session_factory() as session:
        denied_request = SimpleNamespace(state=SimpleNamespace(actor=denied_actor))
        with pytest.raises(HTTPException) as exc_info:
            dependency(
                request=denied_request,
                session=session,
                payload={"id": str(payload_id)},
                entity_id=None,
            )
        assert exc_info.value.status_code == 403
