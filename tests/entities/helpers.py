from __future__ import annotations

import datetime as dt
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
    ArtifactModel,
    ArtifactRecord,
    AuditLogRecord,
    CapabilityKey,
    CapabilityRecord,
    DecisionModel,
    DecisionRecord,
    ProjectModel,
    ProjectRecord,
    RunModel,
    RunRecord,
    TaskModel,
    TaskRecord,
    WorkspaceModel,
    WorkspaceRecord,
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
                WorkspaceModel,
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
                ProjectModel,
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
                TaskModel,
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": "Linked Task",
                    "state": "queued",
                    "description": "Dependency task",
                    "contract": make_coding_task_contract(surface_area=["src/api"]),
                    "definition_of_done": ["done"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                },
            ),
        )
        run = create_run(
            session,
            model_from(
                RunModel,
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
    expires_at: dt.datetime | None = None,
) -> str:
    with session_factory() as session:
        _, raw_token = issue_api_token(
            session,
            actor_id=actor_id,
            scopes=scopes,
            expires_at=expires_at,
        )
        session.commit()
        return raw_token


def seed_workspace(
    session_factory: sessionmaker[Session],
    *,
    slug: str,
    name: str,
) -> uuid.UUID:
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
                    "contract": make_coding_task_contract(surface_area=["src/api"]),
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
    with session_factory() as session:
        artifact = create_artifact(
            session,
            model_from(
                ArtifactModel,
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


def seed_decision(
    session_factory: sessionmaker[Session],
    deps: Dependencies,
    *,
    summary: str,
    decided_at: str,
) -> uuid.UUID:
    with session_factory() as session:
        decision = create_decision(
            session,
            model_from(
                DecisionModel,
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


def make_coding_task_contract(*, surface_area: list[str]) -> dict[str, Any]:
    return {
        "repo": "github.com/agenticqueue/agenticqueue",
        "branch": "main",
        "file_scope": [
            "apps/api/src/agenticqueue_api/app.py",
            "apps/api/src/agenticqueue_api/task_type_registry.py",
        ],
        "surface_area": surface_area,
        "spec": "## Goal\nShip the requested coding-task change.\n",
        "dod_checklist": [
            "Code change landed.",
            "Tests pass.",
        ],
        "autonomy_tier": 3,
        "output": {
            "diff_url": "artifacts/diffs/test.patch",
            "test_report": "artifacts/tests/test.txt",
            "artifacts": [
                {
                    "kind": "patch",
                    "uri": "artifacts/diffs/test.patch",
                    "details": {"format": "unified-diff"},
                }
            ],
            "learnings": [
                {
                    "title": "Capture reusable contract learnings",
                    "type": "pattern",
                    "what_happened": "A task contract needed explicit output artifacts.",
                    "what_learned": "Explicit output artifacts make validation deterministic.",
                    "action_rule": "Add concrete output fields to coding-task contracts.",
                    "applies_when": "A validator consumes task outputs.",
                    "does_not_apply_when": "The task has no durable outputs.",
                    "evidence": ["tests/entities/helpers.py"],
                    "scope": "project",
                    "confidence": "confirmed",
                    "status": "active",
                    "owner": "tests",
                    "review_date": "2026-05-01",
                }
            ],
        },
    }


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


@pytest.fixture(scope="session")
def core_specs_by_resource() -> dict[str, CrudSpec]:
    specs = [
        CrudSpec(
            resource="workspaces",
            entity_type="workspace",
            record_type=WorkspaceRecord,
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
            record_type=ProjectRecord,
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
            record_type=TaskRecord,
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
                "contract": make_coding_task_contract(surface_area=["src/api/task"]),
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
            record_type=RunRecord,
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
            record_type=ArtifactRecord,
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
            record_type=DecisionRecord,
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
    return {spec.resource: spec for spec in specs}


@pytest.fixture
def exercise_core_entity_crud_flow() -> Callable[..., None]:
    def _exercise(
        spec: CrudSpec,
        client: TestClient,
        session_factory: sessionmaker[Session],
        deps: Dependencies,
    ) -> None:
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

        if spec.soft_delete:
            active_list = client.get(
                f"/v1/{spec.resource}",
                headers=auth_headers(token),
                params={"is_active": "true"},
            )
            assert active_list.status_code == 200
            assert str(created_id) in {item["id"] for item in active_list.json()}

        delete_response = client.delete(
            f"/v1/{spec.resource}/{created_id}",
            headers=auth_headers(token),
        )
        assert delete_response.status_code == 204

        if spec.soft_delete:
            assert (
                latest_audit_action(
                    session_factory, entity_type=spec.entity_type, entity_id=created_id
                )
                == "UPDATE"
            )
            assert record_exists(session_factory, spec.record_type, created_id) is True
            assert actor_is_active(session_factory, created_id) is False

            inactive_list = client.get(
                f"/v1/{spec.resource}",
                headers=auth_headers(token),
                params={"is_active": "false"},
            )
            assert inactive_list.status_code == 200
            assert [item["id"] for item in inactive_list.json()] == [str(created_id)]
        else:
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

    return _exercise


@pytest.fixture
def assert_core_entity_auth_failures() -> Callable[..., None]:
    def _assert(
        spec: CrudSpec,
        client: TestClient,
        session_factory: sessionmaker[Session],
        deps: Dependencies,
    ) -> None:
        missing_auth = client.get(f"/v1/{spec.resource}")
        assert_error_shape(missing_auth, status_code=401, error_code="unauthorized")

        expired_actor = seed_actor(
            session_factory,
            handle=f"{spec.entity_type}-expired-admin",
            actor_type="admin",
            display_name=f"{spec.entity_type.capitalize()} Expired Admin",
        )
        expired_token = seed_token(
            session_factory,
            actor_id=expired_actor.id,
            scopes=[spec.read_scope, spec.write_scope],
            expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1),
        )
        expired_response = client.get(
            f"/v1/{spec.resource}",
            headers=auth_headers(expired_token),
        )
        assert_error_shape(expired_response, status_code=401, error_code="unauthorized")

        limited_actor = seed_actor(
            session_factory,
            handle=f"{spec.entity_type}-limited-user",
            actor_type="agent",
            display_name=f"{spec.entity_type.capitalize()} Limited User",
        )
        limited_token = seed_token(
            session_factory,
            actor_id=limited_actor.id,
            scopes=[spec.read_scope],
        )
        forbidden_response = client.post(
            f"/v1/{spec.resource}",
            headers=auth_headers(limited_token),
            json=spec.create_payload(deps),
        )
        assert_error_shape(forbidden_response, status_code=403, error_code="forbidden")

    return _assert
