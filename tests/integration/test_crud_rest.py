from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import (
    ActorModel,
    ActorRecord,
    CapabilityKey,
    CapabilityRecord,
    PolicyModel,
    LearningModel,
    EdgeModel,
    AuditLogRecord,
)
from agenticqueue_api.repo import (
    create_actor,
    create_artifact,
    create_decision,
    create_project,
    create_run,
    create_task,
    create_workspace,
)

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


@dataclass(frozen=True)
class Dependencies:
    actor_id: uuid.UUID
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    task_id: uuid.UUID
    run_id: uuid.UUID


@dataclass(frozen=True)
class CrudSpec:
    resource: str
    entity_type: str
    record_type: type[Any]
    soft_delete: bool
    read_scope: str
    write_scope: str
    create_payload: Callable[[Dependencies], dict[str, Any]]
    filter_params: Callable[[dict[str, Any], Dependencies], dict[str, str]]
    update_payload: dict[str, Any]
    updated_field: str
    updated_value: Any
    seed_sibling: Callable[[sessionmaker[Session], Dependencies], object]


def model_from(model_type: type[Any], payload: dict[str, Any]) -> Any:
    return model_type.model_validate(json.loads(json.dumps(payload)))


def actor_id_for(handle: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{handle}")


def make_actor_payload(
    *,
    handle: str,
    actor_type: str,
    display_name: str,
) -> ActorModel:
    return ActorModel.model_validate(
        {
            "id": str(actor_id_for(handle)),
            "handle": handle,
            "actor_type": actor_type,
            "display_name": display_name,
            "auth_subject": f"{handle}-subject",
            "is_active": True,
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )


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


@pytest.fixture
def deps(session_factory: sessionmaker[Session]) -> Dependencies:
    actor = seed_actor(
        session_factory,
        handle="linked-actor",
        actor_type="agent",
        display_name="Linked Actor",
    )

    with session_factory() as session:
        workspace = create_workspace(
            session,
            model_from(
                __import__(
                    "agenticqueue_api.models", fromlist=["WorkspaceModel"]
                ).WorkspaceModel,
                {
                    "id": str(uuid.uuid4()),
                    "slug": "linked-workspace",
                    "name": "Linked Workspace",
                    "description": "Dependency workspace",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                },
            ),
        )
        project = create_project(
            session,
            model_from(
                __import__(
                    "agenticqueue_api.models", fromlist=["ProjectModel"]
                ).ProjectModel,
                {
                    "id": str(uuid.uuid4()),
                    "workspace_id": str(workspace.id),
                    "slug": "linked-project",
                    "name": "Linked Project",
                    "description": "Dependency project",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                },
            ),
        )
        task = create_task(
            session,
            model_from(
                __import__("agenticqueue_api.models", fromlist=["TaskModel"]).TaskModel,
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": "Linked Task",
                    "state": "queued",
                    "description": "Dependency task",
                    "contract": {"surface_area": ["src/api"]},
                    "definition_of_done": ["done"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                },
            ),
        )
        run = create_run(
            session,
            model_from(
                __import__("agenticqueue_api.models", fromlist=["RunModel"]).RunModel,
                {
                    "id": str(uuid.uuid4()),
                    "task_id": str(task.id),
                    "actor_id": str(actor.id),
                    "status": "running",
                    "started_at": "2026-04-20T00:00:00+00:00",
                    "ended_at": None,
                    "summary": "Linked run",
                    "details": {"attempt": 1},
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                },
            ),
        )
        session.commit()
        return Dependencies(
            actor_id=actor.id,
            workspace_id=workspace.id,
            project_id=project.id,
            task_id=task.id,
            run_id=run.id,
        )


def seed_actor(
    session_factory: sessionmaker[Session],
    *,
    handle: str,
    actor_type: str,
    display_name: str,
) -> ActorModel:
    with session_factory() as session:
        actor = create_actor(
            session,
            make_actor_payload(
                handle=handle,
                actor_type=actor_type,
                display_name=display_name,
            ),
        )
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


def seed_workspace(
    session_factory: sessionmaker[Session],
    *,
    slug: str,
    name: str,
) -> uuid.UUID:
    WorkspaceModel = __import__(
        "agenticqueue_api.models", fromlist=["WorkspaceModel"]
    ).WorkspaceModel
    with session_factory() as session:
        workspace = create_workspace(
            session,
            model_from(
                WorkspaceModel,
                {
                    "id": str(uuid.uuid4()),
                    "slug": slug,
                    "name": name,
                    "description": "Seed workspace",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                },
            ),
        )
        session.commit()
        return workspace.id


def seed_project(
    session_factory: sessionmaker[Session],
    *,
    workspace_id: uuid.UUID,
    slug: str,
    name: str,
) -> uuid.UUID:
    ProjectModel = __import__(
        "agenticqueue_api.models", fromlist=["ProjectModel"]
    ).ProjectModel
    with session_factory() as session:
        project = create_project(
            session,
            model_from(
                ProjectModel,
                {
                    "id": str(uuid.uuid4()),
                    "workspace_id": str(workspace_id),
                    "slug": slug,
                    "name": name,
                    "description": "Seed project",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                },
            ),
        )
        session.commit()
        return project.id


def seed_task(
    session_factory: sessionmaker[Session],
    *,
    project_id: uuid.UUID,
    title: str,
) -> uuid.UUID:
    TaskModel = __import__("agenticqueue_api.models", fromlist=["TaskModel"]).TaskModel
    with session_factory() as session:
        task = create_task(
            session,
            model_from(
                TaskModel,
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project_id),
                    "task_type": "coding-task",
                    "title": title,
                    "state": "queued",
                    "description": "Seed task",
                    "contract": {"surface_area": ["src/api"]},
                    "definition_of_done": ["done"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                },
            ),
        )
        session.commit()
        return task.id


def seed_run(
    session_factory: sessionmaker[Session],
    *,
    task_id: uuid.UUID,
    actor_id: uuid.UUID,
    started_at: str,
) -> uuid.UUID:
    RunModel = __import__("agenticqueue_api.models", fromlist=["RunModel"]).RunModel
    with session_factory() as session:
        run = create_run(
            session,
            model_from(
                RunModel,
                {
                    "id": str(uuid.uuid4()),
                    "task_id": str(task_id),
                    "actor_id": str(actor_id),
                    "status": "running",
                    "started_at": started_at,
                    "ended_at": None,
                    "summary": "Seed run",
                    "details": {"attempt": 1},
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                },
            ),
        )
        session.commit()
        return run.id


def seed_artifact(
    session_factory: sessionmaker[Session],
    *,
    task_id: uuid.UUID,
    run_id: uuid.UUID,
    uri: str,
) -> uuid.UUID:
    ArtifactModelCls = __import__(
        "agenticqueue_api.models", fromlist=["ArtifactModel"]
    ).ArtifactModel
    with session_factory() as session:
        artifact = create_artifact(
            session,
            model_from(
                ArtifactModelCls,
                {
                    "id": str(uuid.uuid4()),
                    "task_id": str(task_id),
                    "run_id": str(run_id),
                    "kind": "diff",
                    "uri": uri,
                    "details": {"bytes": 12},
                    "embedding": None,
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                },
            ),
        )
        session.commit()
        return artifact.id


def latest_audit_action(
    session_factory: sessionmaker[Session],
    *,
    entity_type: str,
    entity_id: uuid.UUID,
) -> str:
    with session_factory() as session:
        statement = (
            sa.select(AuditLogRecord.action)
            .where(
                AuditLogRecord.entity_type == entity_type,
                AuditLogRecord.entity_id == entity_id,
            )
            .order_by(AuditLogRecord.created_at.desc(), AuditLogRecord.id.desc())
        )
        action = session.scalars(statement).first()
        assert action is not None
        return action


def record_exists(
    session_factory: sessionmaker[Session],
    record_type: type[Any],
    entity_id: uuid.UUID,
) -> bool:
    with session_factory() as session:
        return session.get(record_type, entity_id) is not None


def actor_is_active(
    session_factory: sessionmaker[Session],
    actor_id: uuid.UUID,
) -> bool:
    with session_factory() as session:
        record = session.get(ActorRecord, actor_id)
        assert record is not None
        return record.is_active


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_error_shape(
    response: Any,
    *,
    status_code: int,
    error_code: str,
) -> None:
    assert response.status_code == status_code
    body = response.json()
    assert body["error_code"] == error_code
    assert isinstance(body["message"], str)
    assert "details" in body


def core_specs() -> list[CrudSpec]:
    return [
        CrudSpec(
            resource="workspaces",
            entity_type="workspace",
            record_type=__import__(
                "agenticqueue_api.models", fromlist=["WorkspaceRecord"]
            ).WorkspaceRecord,
            soft_delete=False,
            read_scope="workspace:read",
            write_scope="workspace:write",
            create_payload=lambda deps: {
                "id": str(uuid.uuid4()),
                "slug": "workspace-alpha",
                "name": "Workspace Alpha",
                "description": "Workspace create path",
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
            },
            filter_params=lambda created, deps: {"slug": created["slug"]},
            update_payload={"name": "Workspace Alpha Updated"},
            updated_field="name",
            updated_value="Workspace Alpha Updated",
            seed_sibling=lambda sf, deps: seed_workspace(
                sf, slug="workspace-zeta", name="Workspace Zeta"
            ),
        ),
        CrudSpec(
            resource="projects",
            entity_type="project",
            record_type=__import__(
                "agenticqueue_api.models", fromlist=["ProjectRecord"]
            ).ProjectRecord,
            soft_delete=False,
            read_scope="project:read",
            write_scope="project:write",
            create_payload=lambda deps: {
                "id": str(uuid.uuid4()),
                "workspace_id": str(deps.workspace_id),
                "slug": "project-alpha",
                "name": "Project Alpha",
                "description": "Project create path",
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
            },
            filter_params=lambda created, deps: {"slug": created["slug"]},
            update_payload={"description": "Project Alpha Updated"},
            updated_field="description",
            updated_value="Project Alpha Updated",
            seed_sibling=lambda sf, deps: seed_project(
                sf,
                workspace_id=seed_workspace(
                    sf, slug="workspace-for-project", name="Workspace For Project"
                ),
                slug="project-zeta",
                name="Project Zeta",
            ),
        ),
        CrudSpec(
            resource="tasks",
            entity_type="task",
            record_type=__import__(
                "agenticqueue_api.models", fromlist=["TaskRecord"]
            ).TaskRecord,
            soft_delete=False,
            read_scope="task:read",
            write_scope="task:write",
            create_payload=lambda deps: {
                "id": str(uuid.uuid4()),
                "project_id": str(deps.project_id),
                "task_type": "coding-task",
                "title": "Task Alpha",
                "state": "queued",
                "description": "Task create path",
                "contract": {"surface_area": ["src/api/task"]},
                "definition_of_done": ["done"],
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
            },
            filter_params=lambda created, deps: {"title": created["title"]},
            update_payload={"state": "in_progress"},
            updated_field="state",
            updated_value="in_progress",
            seed_sibling=lambda sf, deps: seed_task(
                sf,
                project_id=seed_project(
                    sf,
                    workspace_id=deps.workspace_id,
                    slug="project-for-task",
                    name="Project For Task",
                ),
                title="Task Zeta",
            ),
        ),
        CrudSpec(
            resource="runs",
            entity_type="run",
            record_type=__import__(
                "agenticqueue_api.models", fromlist=["RunRecord"]
            ).RunRecord,
            soft_delete=False,
            read_scope="run:read",
            write_scope="run:write",
            create_payload=lambda deps: {
                "id": str(uuid.uuid4()),
                "task_id": str(deps.task_id),
                "actor_id": str(deps.actor_id),
                "status": "running",
                "started_at": "2026-04-20T00:01:00+00:00",
                "ended_at": None,
                "summary": "Run Alpha",
                "details": {"attempt": 1},
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
            },
            filter_params=lambda created, deps: {"started_at": created["started_at"]},
            update_payload={"summary": "Run Alpha Updated"},
            updated_field="summary",
            updated_value="Run Alpha Updated",
            seed_sibling=lambda sf, deps: seed_run(
                sf,
                task_id=deps.task_id,
                actor_id=deps.actor_id,
                started_at="2026-04-20T00:05:00+00:00",
            ),
        ),
        CrudSpec(
            resource="artifacts",
            entity_type="artifact",
            record_type=__import__(
                "agenticqueue_api.models", fromlist=["ArtifactRecord"]
            ).ArtifactRecord,
            soft_delete=False,
            read_scope="artifact:read",
            write_scope="artifact:write",
            create_payload=lambda deps: {
                "id": str(uuid.uuid4()),
                "task_id": str(deps.task_id),
                "run_id": str(deps.run_id),
                "kind": "diff",
                "uri": "file:///artifact-alpha.patch",
                "details": {"bytes": 12},
                "embedding": None,
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
            },
            filter_params=lambda created, deps: {"run_id": created["run_id"]},
            update_payload={"uri": "file:///artifact-alpha-updated.patch"},
            updated_field="uri",
            updated_value="file:///artifact-alpha-updated.patch",
            seed_sibling=lambda sf, deps: seed_artifact(
                sf,
                task_id=deps.task_id,
                run_id=seed_run(
                    sf,
                    task_id=deps.task_id,
                    actor_id=deps.actor_id,
                    started_at="2026-04-20T00:10:00+00:00",
                ),
                uri="file:///artifact-zeta.patch",
            ),
        ),
        CrudSpec(
            resource="decisions",
            entity_type="decision",
            record_type=__import__(
                "agenticqueue_api.models", fromlist=["DecisionRecord"]
            ).DecisionRecord,
            soft_delete=False,
            read_scope="decision:read",
            write_scope="decision:write",
            create_payload=lambda deps: {
                "id": str(uuid.uuid4()),
                "task_id": str(deps.task_id),
                "run_id": str(deps.run_id),
                "actor_id": str(deps.actor_id),
                "summary": "Decision Alpha",
                "rationale": "Initial rationale",
                "decided_at": "2026-04-20T00:00:00+00:00",
                "embedding": None,
                "created_at": "2026-04-20T00:00:00+00:00",
            },
            filter_params=lambda created, deps: {"decided_at": created["decided_at"]},
            update_payload={"rationale": "Updated rationale"},
            updated_field="rationale",
            updated_value="Updated rationale",
            seed_sibling=lambda sf, deps: seed_decision(
                sf,
                deps,
                summary="Decision Zeta",
                decided_at="2026-04-20T00:05:00+00:00",
            ),
        ),
        CrudSpec(
            resource="actors",
            entity_type="actor",
            record_type=ActorRecord,
            soft_delete=True,
            read_scope="actor:read",
            write_scope="actor:write",
            create_payload=lambda deps: {
                "id": str(uuid.uuid4()),
                "handle": "actor-alpha",
                "actor_type": "agent",
                "display_name": "Actor Alpha",
                "auth_subject": "actor-alpha-subject",
                "is_active": True,
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
            },
            filter_params=lambda created, deps: {"handle": created["handle"]},
            update_payload={"display_name": "Actor Alpha Updated"},
            updated_field="display_name",
            updated_value="Actor Alpha Updated",
            seed_sibling=lambda sf, deps: seed_actor(
                sf,
                handle="actor-zeta",
                actor_type="agent",
                display_name="Actor Zeta",
            ),
        ),
    ]


def seed_decision(
    session_factory: sessionmaker[Session],
    deps: Dependencies,
    *,
    summary: str,
    decided_at: str,
) -> uuid.UUID:
    DecisionModelCls = __import__(
        "agenticqueue_api.models", fromlist=["DecisionModel"]
    ).DecisionModel
    with session_factory() as session:
        decision = create_decision(
            session,
            model_from(
                DecisionModelCls,
                {
                    "id": str(uuid.uuid4()),
                    "task_id": str(deps.task_id),
                    "run_id": str(deps.run_id),
                    "actor_id": str(deps.actor_id),
                    "summary": summary,
                    "rationale": "Sibling rationale",
                    "decided_at": decided_at,
                    "embedding": None,
                    "created_at": decided_at,
                },
            ),
        )
        session.commit()
        return decision.id


def test_openapi_route_is_available_with_bearer_auth(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    actor = seed_actor(
        session_factory,
        handle="openapi-admin",
        actor_type="admin",
        display_name="OpenAPI Admin",
    )
    token = seed_token(session_factory, actor_id=actor.id, scopes=["admin"])

    response = client.get("/openapi.json", headers=auth_headers(token))

    assert response.status_code == 200
    assert "/v1/workspaces" in response.json()["paths"]
    assert "/v1/edges/{entity_id}" in response.json()["paths"]


def test_missing_auth_and_wrong_scope_are_structured(
    client: TestClient,
    session_factory: sessionmaker[Session],
    deps: Dependencies,
) -> None:
    actor = seed_actor(
        session_factory,
        handle="scope-user",
        actor_type="agent",
        display_name="Scope User",
    )
    token = seed_token(session_factory, actor_id=actor.id, scopes=["workspace:read"])

    missing_auth = client.get("/v1/workspaces")
    assert_error_shape(missing_auth, status_code=401, error_code="unauthorized")

    forbidden = client.post(
        "/v1/workspaces",
        headers=auth_headers(token),
        json=core_specs()[0].create_payload(deps),
    )
    assert_error_shape(forbidden, status_code=403, error_code="forbidden")


@pytest.mark.parametrize("spec_index", range(6))
def test_core_entities_support_crud_filtering_and_audit(
    spec_index: int,
    client: TestClient,
    session_factory: sessionmaker[Session],
    deps: Dependencies,
) -> None:
    spec = core_specs()[spec_index]
    actor = seed_actor(
        session_factory,
        handle=f"{spec.entity_type}-admin",
        actor_type="admin",
        display_name=f"{spec.entity_type.capitalize()} Admin",
    )
    token = seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=[spec.read_scope, spec.write_scope],
    )
    spec.seed_sibling(session_factory, deps)

    create_response = client.post(
        f"/v1/{spec.resource}",
        headers=auth_headers(token),
        json=spec.create_payload(deps),
    )
    assert create_response.status_code == 201
    created = create_response.json()
    created_id = uuid.UUID(created["id"])
    assert (
        latest_audit_action(
            session_factory, entity_type=spec.entity_type, entity_id=created_id
        )
        == "CREATE"
    )

    get_response = client.get(
        f"/v1/{spec.resource}/{created_id}",
        headers=auth_headers(token),
    )
    assert get_response.status_code == 200
    assert get_response.json()["id"] == str(created_id)

    list_response = client.get(
        f"/v1/{spec.resource}",
        headers=auth_headers(token),
        params=spec.filter_params(created, deps),
    )
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [str(created_id)]

    update_response = client.patch(
        f"/v1/{spec.resource}/{created_id}",
        headers=auth_headers(token),
        json=spec.update_payload,
    )
    assert update_response.status_code == 200
    assert update_response.json()[spec.updated_field] == spec.updated_value
    assert (
        latest_audit_action(
            session_factory, entity_type=spec.entity_type, entity_id=created_id
        )
        == "UPDATE"
    )

    delete_response = client.delete(
        f"/v1/{spec.resource}/{created_id}",
        headers=auth_headers(token),
    )
    assert delete_response.status_code == 204
    assert (
        latest_audit_action(
            session_factory, entity_type=spec.entity_type, entity_id=created_id
        )
        == "DELETE"
    )
    assert record_exists(session_factory, spec.record_type, created_id) is False

    missing_response = client.get(
        f"/v1/{spec.resource}/{uuid.uuid4()}",
        headers=auth_headers(token),
    )
    assert_error_shape(missing_response, status_code=404, error_code="not_found")


def test_actor_soft_delete_and_boolean_filtering(
    client: TestClient,
    session_factory: sessionmaker[Session],
    deps: Dependencies,
) -> None:
    spec = core_specs()[6]
    actor = seed_actor(
        session_factory,
        handle="actor-admin",
        actor_type="admin",
        display_name="Actor Admin",
    )
    token = seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=[spec.read_scope, spec.write_scope],
    )
    spec.seed_sibling(session_factory, deps)

    create_response = client.post(
        "/v1/actors",
        headers=auth_headers(token),
        json=spec.create_payload(deps),
    )
    assert create_response.status_code == 201
    created_id = uuid.UUID(create_response.json()["id"])

    active_list = client.get(
        "/v1/actors",
        headers=auth_headers(token),
        params={"is_active": "true"},
    )
    assert active_list.status_code == 200
    assert str(created_id) in {item["id"] for item in active_list.json()}

    delete_response = client.delete(
        f"/v1/actors/{created_id}",
        headers=auth_headers(token),
    )
    assert delete_response.status_code == 204
    assert (
        latest_audit_action(session_factory, entity_type="actor", entity_id=created_id)
        == "UPDATE"
    )
    assert record_exists(session_factory, ActorRecord, created_id) is True
    assert actor_is_active(session_factory, created_id) is False

    inactive_list = client.get(
        "/v1/actors",
        headers=auth_headers(token),
        params={"is_active": "false"},
    )
    assert inactive_list.status_code == 200
    assert [item["id"] for item in inactive_list.json()] == [str(created_id)]


def test_policy_learning_and_edge_filters_cover_int_date_and_enum_paths(
    client: TestClient,
    session_factory: sessionmaker[Session],
    deps: Dependencies,
) -> None:
    actor = seed_actor(
        session_factory,
        handle="meta-admin",
        actor_type="admin",
        display_name="Meta Admin",
    )
    token = seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=[
            "policy:read",
            "policy:write",
            "learning:read",
            "learning:write",
            "edge:read",
            "edge:write",
        ],
    )

    policy_payload = model_from(
        PolicyModel,
        {
            "id": str(uuid.uuid4()),
            "workspace_id": str(deps.workspace_id),
            "name": "default-coding",
            "version": "1.0.0",
            "hitl_required": False,
            "autonomy_tier": 3,
            "body": {"rule": "allow"},
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        },
    ).model_dump(mode="json")
    policy_response = client.post(
        "/v1/policies", headers=auth_headers(token), json=policy_payload
    )
    assert policy_response.status_code == 201
    assert (
        client.get(
            "/v1/policies",
            headers=auth_headers(token),
            params={"autonomy_tier": "3"},
        ).status_code
        == 200
    )

    learning_payload = model_from(
        LearningModel,
        {
            "id": str(uuid.uuid4()),
            "task_id": str(deps.task_id),
            "owner_actor_id": str(deps.actor_id),
            "title": "Learning Alpha",
            "learning_type": "pattern",
            "what_happened": "A thing happened",
            "what_learned": "A thing was learned",
            "action_rule": "Do the better thing",
            "applies_when": "Always",
            "does_not_apply_when": "Never",
            "evidence": ["run:1"],
            "scope": "project",
            "confidence": "confirmed",
            "status": "active",
            "review_date": "2026-04-21",
            "embedding": None,
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        },
    ).model_dump(mode="json")
    learning_response = client.post(
        "/v1/learnings", headers=auth_headers(token), json=learning_payload
    )
    assert learning_response.status_code == 201
    assert (
        client.get(
            "/v1/learnings",
            headers=auth_headers(token),
            params={"review_date": "2026-04-21"},
        ).status_code
        == 200
    )

    edge_payload = model_from(
        EdgeModel,
        {
            "id": str(uuid.uuid4()),
            "src_entity_type": "task",
            "src_id": str(deps.task_id),
            "dst_entity_type": "project",
            "dst_id": str(deps.project_id),
            "relation": "depends_on",
            "metadata": {},
            "created_by": str(deps.actor_id),
            "created_at": "2026-04-20T00:00:00+00:00",
        },
    ).model_dump(mode="json")
    edge_response = client.post(
        "/v1/edges", headers=auth_headers(token), json=edge_payload
    )
    assert edge_response.status_code == 201
    assert (
        client.get(
            "/v1/edges",
            headers=auth_headers(token),
            params={"relation": "depends_on"},
        ).status_code
        == 200
    )


def test_duplicate_create_invalid_filter_invalid_value_invalid_payload_and_immutable_patch_are_structured(
    client: TestClient,
    session_factory: sessionmaker[Session],
    deps: Dependencies,
) -> None:
    actor = seed_actor(
        session_factory,
        handle="workspace-admin",
        actor_type="admin",
        display_name="Workspace Admin",
    )
    token = seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=["workspace:read", "workspace:write", "actor:read", "edge:read"],
    )

    workspace_payload = core_specs()[0].create_payload(deps)
    create_response = client.post(
        "/v1/workspaces",
        headers=auth_headers(token),
        json=workspace_payload,
    )
    assert create_response.status_code == 201
    created_id = create_response.json()["id"]

    duplicate_response = client.post(
        "/v1/workspaces",
        headers=auth_headers(token),
        json={
            **core_specs()[0].create_payload(deps),
            "slug": workspace_payload["slug"],
        },
    )
    assert_error_shape(duplicate_response, status_code=409, error_code="conflict")

    conflicting_slug = "workspace-conflict"
    seed_workspace(session_factory, slug=conflicting_slug, name="Workspace Conflict")
    conflict_update = client.patch(
        f"/v1/workspaces/{created_id}",
        headers=auth_headers(token),
        json={"slug": conflicting_slug},
    )
    assert_error_shape(conflict_update, status_code=409, error_code="conflict")

    unknown_filter_response = client.get(
        "/v1/workspaces",
        headers=auth_headers(token),
        params={"unknown": "value"},
    )
    assert_error_shape(
        unknown_filter_response, status_code=400, error_code="bad_request"
    )

    invalid_bool_filter = client.get(
        "/v1/actors",
        headers=auth_headers(token),
        params={"is_active": "maybe"},
    )
    assert_error_shape(invalid_bool_filter, status_code=400, error_code="bad_request")

    invalid_enum_filter = client.get(
        "/v1/edges",
        headers=auth_headers(token),
        params={"relation": "not-a-relation"},
    )
    assert_error_shape(invalid_enum_filter, status_code=400, error_code="bad_request")

    invalid_payload = client.post(
        "/v1/workspaces",
        headers=auth_headers(token),
        json={"slug": "missing-fields"},
    )
    assert_error_shape(invalid_payload, status_code=422, error_code="validation_error")

    immutable_patch = client.patch(
        f"/v1/workspaces/{created_id}",
        headers=auth_headers(token),
        json={"id": str(uuid.uuid4())},
    )
    assert_error_shape(immutable_patch, status_code=400, error_code="bad_request")
