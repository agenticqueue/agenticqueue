from __future__ import annotations

import json
from pathlib import Path
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.capabilities import grant_capability
from agenticqueue_api.cli import app as cli_app
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.learnings import DraftLearningRecord, LearningPromotionService
from agenticqueue_api.models import (
    ActorModel,
    AuditLogRecord,
    CapabilityKey,
    CapabilityRecord,
    LearningModel,
    LearningRecord,
    ProjectModel,
    RunModel,
    TaskModel,
    TaskRecord,
    WorkspaceModel,
)
from agenticqueue_api.repo import (
    create_actor,
    create_learning,
    create_project,
    create_run,
    create_task,
    create_workspace,
)
from agenticqueue_api.schemas.learning import LearningScope, LearningStatus

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

runner = CliRunner()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _example_contract() -> dict[str, object]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    return json.loads(path.read_text(encoding="utf-8"))


def actor_id_for(handle: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{handle}")


def make_actor_payload(
    *,
    handle: str,
    actor_type: str = "agent",
) -> ActorModel:
    actor_id = actor_id_for(handle)
    return ActorModel.model_validate(
        {
            "id": str(actor_id),
            "handle": handle,
            "actor_type": actor_type,
            "display_name": handle.replace("-", " ").title(),
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
def db_session(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app(session_factory=session_factory)
    with TestClient(app) as test_client:
        yield test_client


def seed_actor(
    session_factory: sessionmaker[Session],
    *,
    handle: str,
    actor_type: str = "agent",
) -> ActorModel:
    with session_factory() as session:
        actor = create_actor(
            session,
            make_actor_payload(handle=handle, actor_type=actor_type),
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


def auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": str(uuid.uuid4()),
    }


def seed_workspace_project_task_run(
    session: Session,
    *,
    suffix: str,
    created_at: str,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    workspace = create_workspace(
        session,
        WorkspaceModel.model_validate(
            {
                "id": str(uuid.uuid4()),
                "slug": f"workspace-{suffix}",
                "name": f"Workspace {suffix}",
                "description": "Learning promotion test workspace",
                "created_at": created_at,
                "updated_at": created_at,
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
                "description": "Learning promotion test project",
                "created_at": created_at,
                "updated_at": created_at,
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
                "state": "done",
                "description": "Learning promotion test task",
                "contract": _example_contract(),
                "definition_of_done": _example_contract()["dod_checklist"],
                "created_at": created_at,
                "updated_at": created_at,
            }
        ),
    )
    actor = create_actor(
        session,
        make_actor_payload(handle=f"actor-{suffix}"),
    )
    run = create_run(
        session,
        RunModel.model_validate(
            {
                "id": str(uuid.uuid4()),
                "task_id": str(task.id),
                "actor_id": str(actor.id),
                "status": "completed",
                "started_at": created_at,
                "ended_at": created_at,
                "summary": f"Run {suffix}",
                "details": {},
                "created_at": created_at,
                "updated_at": created_at,
            }
        ),
    )
    return project.id, task.id, actor.id, run.id


def make_learning_payload(
    suffix: int,
    *,
    task_id: uuid.UUID | None,
    title: str,
    action_rule: str,
    scope: str,
    created_at: str,
) -> LearningModel:
    return LearningModel.model_validate(
        {
            "id": f"00000000-0000-0000-0000-{suffix:012d}",
            "task_id": None if task_id is None else str(task_id),
            "owner_actor_id": None,
            "owner": "agenticqueue-auto-draft",
            "title": title,
            "learning_type": "pattern",
            "what_happened": "A reusable learning was captured.",
            "what_learned": "The pattern should be reused later.",
            "action_rule": action_rule,
            "applies_when": "The same integration path appears again.",
            "does_not_apply_when": "The dependency graph changed materially.",
            "evidence": [f"artifact://{suffix}"],
            "scope": scope,
            "confidence": "confirmed",
            "status": LearningStatus.ACTIVE.value,
            "review_date": "2026-05-04",
            "embedding": None,
            "promotion_eligible": False,
            "created_at": created_at,
            "updated_at": created_at,
        }
    )


def latest_audit_row(
    session: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
) -> AuditLogRecord:
    statement = (
        sa.select(AuditLogRecord)
        .where(
            AuditLogRecord.entity_type == entity_type,
            AuditLogRecord.entity_id == entity_id,
            AuditLogRecord.action == action,
        )
        .order_by(AuditLogRecord.created_at.desc(), AuditLogRecord.id.desc())
    )
    row = session.scalars(statement).first()
    assert row is not None
    return row


def test_auto_promote_candidates_flags_task_learning_from_merge_history(
    db_session: Session,
) -> None:
    project_id, task_id, _, _ = seed_workspace_project_task_run(
        db_session,
        suffix="task-a",
        created_at="2026-04-20T00:00:00+00:00",
    )
    _, second_task_id, _, second_run_id = seed_workspace_project_task_run(
        db_session,
        suffix="task-b",
        created_at="2026-04-20T00:05:00+00:00",
    )
    learning = create_learning(
        db_session,
        make_learning_payload(
            901,
            task_id=task_id,
            title="Capture retry recovery path",
            action_rule="Start from the last failing test before rerunning the suite.",
            scope=LearningScope.TASK.value,
            created_at="2026-04-20T00:00:00+00:00",
        ),
    )
    db_session.add(
        DraftLearningRecord(
            task_id=second_task_id,
            run_id=second_run_id,
            payload={
                "title": learning.title,
                "type": "pattern",
                "what_happened": learning.what_happened,
                "what_learned": learning.what_learned,
                "action_rule": learning.action_rule,
                "applies_when": learning.applies_when,
                "does_not_apply_when": learning.does_not_apply_when,
                "evidence": ["artifact://draft-task"],
                "scope": LearningScope.TASK.value,
                "confidence": "confirmed",
                "status": "active",
                "owner": learning.owner,
                "review_date": "2026-05-04",
            },
            draft_status="confirmed",
            confirmed_learning_id=learning.id,
        )
    )
    db_session.flush()

    candidates = LearningPromotionService(db_session).auto_promote_candidates()

    assert project_id is not None
    assert [candidate.id for candidate in candidates] == [learning.id]
    db_record = db_session.get(LearningRecord, learning.id)
    assert db_record is not None
    assert db_record.promotion_eligible is True


def test_auto_promote_candidates_returns_one_global_candidate_across_projects(
    db_session: Session,
) -> None:
    title = "Canonical validator retry learning"
    action_rule = "Fix the payload shape before the next full rerun."

    _, task_one, _, _ = seed_workspace_project_task_run(
        db_session,
        suffix="global-a",
        created_at="2026-04-20T00:00:00+00:00",
    )
    _, task_two, _, _ = seed_workspace_project_task_run(
        db_session,
        suffix="global-b",
        created_at="2026-04-20T00:10:00+00:00",
    )
    _, task_three, _, _ = seed_workspace_project_task_run(
        db_session,
        suffix="global-c",
        created_at="2026-04-20T00:20:00+00:00",
    )

    first = create_learning(
        db_session,
        make_learning_payload(
            911,
            task_id=task_one,
            title=title,
            action_rule=action_rule,
            scope=LearningScope.PROJECT.value,
            created_at="2026-04-20T00:00:00+00:00",
        ),
    )
    second = create_learning(
        db_session,
        make_learning_payload(
            912,
            task_id=task_two,
            title=title,
            action_rule=action_rule,
            scope=LearningScope.PROJECT.value,
            created_at="2026-04-20T00:10:00+00:00",
        ),
    )
    third = create_learning(
        db_session,
        make_learning_payload(
            913,
            task_id=task_three,
            title=title,
            action_rule=action_rule,
            scope=LearningScope.PROJECT.value,
            created_at="2026-04-20T00:20:00+00:00",
        ),
    )

    candidates = LearningPromotionService(db_session).auto_promote_candidates()

    first_record = db_session.get(LearningRecord, first.id)
    second_record = db_session.get(LearningRecord, second.id)
    third_record = db_session.get(LearningRecord, third.id)

    assert first_record is not None
    assert second_record is not None
    assert third_record is not None
    assert [candidate.id for candidate in candidates] == [first.id]
    assert first_record.promotion_eligible is True
    assert second_record.promotion_eligible is False
    assert third_record.promotion_eligible is False


def test_promote_learning_updates_scope_and_writes_audit_entry(
    db_session: Session,
) -> None:
    title = "Canonical queue retry learning"
    action_rule = "Reuse the last known-good command before broad refactors."

    _, task_one, _, _ = seed_workspace_project_task_run(
        db_session,
        suffix="promote-a",
        created_at="2026-04-20T00:00:00+00:00",
    )
    _, task_two, _, _ = seed_workspace_project_task_run(
        db_session,
        suffix="promote-b",
        created_at="2026-04-20T00:10:00+00:00",
    )
    _, task_three, _, _ = seed_workspace_project_task_run(
        db_session,
        suffix="promote-c",
        created_at="2026-04-20T00:20:00+00:00",
    )
    learning = create_learning(
        db_session,
        make_learning_payload(
            921,
            task_id=task_one,
            title=title,
            action_rule=action_rule,
            scope=LearningScope.PROJECT.value,
            created_at="2026-04-20T00:00:00+00:00",
        ),
    )
    create_learning(
        db_session,
        make_learning_payload(
            922,
            task_id=task_two,
            title=title,
            action_rule=action_rule,
            scope=LearningScope.PROJECT.value,
            created_at="2026-04-20T00:10:00+00:00",
        ),
    )
    create_learning(
        db_session,
        make_learning_payload(
            923,
            task_id=task_three,
            title=title,
            action_rule=action_rule,
            scope=LearningScope.PROJECT.value,
            created_at="2026-04-20T00:20:00+00:00",
        ),
    )

    service = LearningPromotionService(db_session)
    service.auto_promote_candidates()
    promoted = service.promote(
        learning_id=learning.id,
        target_scope=LearningScope.GLOBAL,
    )

    assert promoted.scope == LearningScope.GLOBAL.value
    assert promoted.promotion_eligible is False

    audit_row = latest_audit_row(
        db_session,
        entity_type="learning",
        entity_id=learning.id,
        action="UPDATE",
    )
    assert audit_row.after is not None
    assert audit_row.after["scope"] == LearningScope.GLOBAL.value
    assert audit_row.after["promotion_eligible"] is False


def test_learning_promote_cli_updates_scope(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _, task_id, _, _ = seed_workspace_project_task_run(
            session,
            suffix="cli-promote",
            created_at="2026-04-20T00:00:00+00:00",
        )
        learning = create_learning(
            session,
            make_learning_payload(
                924,
                task_id=task_id,
                title="CLI promotion candidate",
                action_rule="Promote reusable learnings through the shared CLI.",
                scope=LearningScope.TASK.value,
                created_at="2026-04-20T00:00:00+00:00",
            ),
        )
        session.commit()
        learning_id = learning.id

    result = runner.invoke(
        cli_app,
        ["learning", "promote", str(learning_id), LearningScope.PROJECT.value],
    )

    assert result.exit_code == 0
    promoted = LearningModel.model_validate(json.loads(result.stdout))
    assert promoted.id == learning_id
    assert promoted.scope == LearningScope.PROJECT

    with session_factory() as session:
        db_record = session.get(LearningRecord, learning_id)
        assert db_record is not None
        assert db_record.scope == LearningScope.PROJECT.value


def test_promote_endpoint_requires_capability_and_updates_learning(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    actor = seed_actor(session_factory, handle="promote-route")
    token = seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=["learning:write"],
    )

    with session_factory() as session:
        _, task_one, _, _ = seed_workspace_project_task_run(
            session,
            suffix="route-a",
            created_at="2026-04-20T00:00:00+00:00",
        )
        project_id = session.scalar(
            sa.select(TaskRecord.project_id).where(TaskRecord.id == task_one)
        )
        assert project_id is not None
        learning = create_learning(
            session,
            make_learning_payload(
                931,
                task_id=task_one,
                title="Route promotion candidate",
                action_rule="Promote once the same learning appears across projects.",
                scope=LearningScope.TASK.value,
                created_at="2026-04-20T00:00:00+00:00",
            ),
        )
        session.commit()

    with session_factory() as session:
        grant_capability(
            session,
            actor_id=actor.id,
            capability=CapabilityKey.PROMOTE_LEARNING,
            scope={"project_id": str(project_id)},
        )
        session.commit()

    response = client.post(
        f"/v1/learnings/{learning.id}/promote",
        headers=auth_headers(token),
        json={"target_scope": "project"},
    )

    assert response.status_code == 200
    assert response.json()["learning"]["scope"] == LearningScope.PROJECT.value
    assert response.json()["learning"]["promotion_eligible"] is False
