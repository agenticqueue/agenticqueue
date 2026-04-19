from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import (
    ActorModel,
    ArtifactModel,
    AuditLogModel,
    CapabilityModel,
    DecisionModel,
    LearningModel,
    PacketVersionModel,
    PolicyModel,
    ProjectModel,
    RunModel,
    TaskModel,
    WorkspaceModel,
)
from agenticqueue_api.models.shared import CreatedSchema
from agenticqueue_api.repo import (
    create_actor,
    create_artifact,
    create_audit_log,
    create_capability,
    create_decision,
    create_learning,
    create_packet_version,
    create_policy,
    create_project,
    create_run,
    create_task,
    create_workspace,
    get_actor,
    get_artifact,
    get_audit_log,
    get_capability,
    get_decision,
    get_learning,
    get_packet_version,
    get_policy,
    get_project,
    get_run,
    get_task,
    get_workspace,
)

WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"
PROJECT_ID = "00000000-0000-0000-0000-000000000002"
TASK_ID = "00000000-0000-0000-0000-000000000003"
ACTOR_ID = "00000000-0000-0000-0000-000000000004"
RUN_ID = "00000000-0000-0000-0000-000000000005"
ARTIFACT_ID = "00000000-0000-0000-0000-000000000006"
DECISION_ID = "00000000-0000-0000-0000-000000000007"
CAPABILITY_ID = "00000000-0000-0000-0000-000000000008"
POLICY_ID = "00000000-0000-0000-0000-000000000009"
LEARNING_ID = "00000000-0000-0000-0000-00000000000a"
PACKET_VERSION_ID = "00000000-0000-0000-0000-00000000000b"
AUDIT_LOG_ID = "00000000-0000-0000-0000-00000000000c"

ENTITY_FIXTURES = {
    "workspace": {
        "id": WORKSPACE_ID,
        "slug": "core-workspace",
        "name": "Core Workspace",
        "description": "Main coordination workspace.",
        "created_at": "2026-04-19T12:00:00+00:00",
        "updated_at": "2026-04-19T12:00:00+00:00",
    },
    "actor": {
        "id": ACTOR_ID,
        "handle": "codex",
        "actor_type": "agent",
        "display_name": "Codex",
        "auth_subject": "codex-agent",
        "is_active": True,
        "created_at": "2026-04-19T12:00:00+00:00",
        "updated_at": "2026-04-19T12:00:00+00:00",
    },
    "project": {
        "id": PROJECT_ID,
        "workspace_id": WORKSPACE_ID,
        "slug": "agenticqueue-core",
        "name": "AgenticQueue Core",
        "description": "Public coordination plane.",
        "created_at": "2026-04-19T12:01:00+00:00",
        "updated_at": "2026-04-19T12:01:00+00:00",
    },
    "task": {
        "id": TASK_ID,
        "project_id": PROJECT_ID,
        "task_type": "coding-task",
        "title": "Land entity models",
        "state": "in_progress",
        "description": "Implement Phase 1 entities.",
        "contract": {"summary": "Create Phase 1 entities"},
        "definition_of_done": ["Create models", "Add CRUD tests"],
        "created_at": "2026-04-19T12:02:00+00:00",
        "updated_at": "2026-04-19T12:02:00+00:00",
    },
    "run": {
        "id": RUN_ID,
        "task_id": TASK_ID,
        "actor_id": ACTOR_ID,
        "status": "succeeded",
        "started_at": "2026-04-19T12:03:00+00:00",
        "ended_at": "2026-04-19T12:08:00+00:00",
        "summary": "Entity slice landed cleanly.",
        "details": {"duration_seconds": 300},
        "created_at": "2026-04-19T12:03:00+00:00",
        "updated_at": "2026-04-19T12:08:00+00:00",
    },
    "artifact": {
        "id": ARTIFACT_ID,
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "kind": "diff",
        "uri": "file://artifacts/entity-models.diff",
        "details": {"line_count": 240},
        "created_at": "2026-04-19T12:09:00+00:00",
        "updated_at": "2026-04-19T12:09:00+00:00",
    },
    "decision": {
        "id": DECISION_ID,
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "actor_id": ACTOR_ID,
        "summary": "Use one table per entity.",
        "rationale": "It keeps Phase 1 explicit and testable.",
        "decided_at": "2026-04-19T12:10:00+00:00",
        "created_at": "2026-04-19T12:10:00+00:00",
    },
    "capability": {
        "id": CAPABILITY_ID,
        "actor_id": ACTOR_ID,
        "capability_key": "write_repo",
        "scope": "project:agenticqueue-core",
        "granted_by_actor_id": ACTOR_ID,
        "is_active": True,
        "created_at": "2026-04-19T12:11:00+00:00",
        "updated_at": "2026-04-19T12:11:00+00:00",
    },
    "policy": {
        "id": POLICY_ID,
        "workspace_id": WORKSPACE_ID,
        "name": "default-coding",
        "version": "v1",
        "hitl_required": False,
        "autonomy_tier": 3,
        "body": {"hitl_required": False, "autonomy_tier": 3},
        "created_at": "2026-04-19T12:12:00+00:00",
        "updated_at": "2026-04-19T12:12:00+00:00",
    },
    "learning": {
        "id": LEARNING_ID,
        "task_id": TASK_ID,
        "owner_actor_id": ACTOR_ID,
        "title": "Keep models explicit in Phase 1",
        "learning_type": "pattern",
        "what_happened": "The entity slice needed stable schema contracts.",
        "what_learned": "Explicit tables simplify later graph and policy work.",
        "action_rule": "Prefer named columns over opaque JSON blobs in Phase 1.",
        "applies_when": "Core entities are first introduced.",
        "does_not_apply_when": "The field is intentionally schemaless payload.",
        "evidence": ["AQ-39"],
        "scope": "project",
        "confidence": "confirmed",
        "status": "active",
        "review_date": "2026-05-01",
        "created_at": "2026-04-19T12:13:00+00:00",
        "updated_at": "2026-04-19T12:13:00+00:00",
    },
    "packet_version": {
        "id": PACKET_VERSION_ID,
        "task_id": TASK_ID,
        "packet_hash": "sha256:1b6c95b315f59552a2796774c7ce8f434609f97efc2d89ab0d34c3a6646187f7",
        "payload": {"task_id": TASK_ID, "version": 1},
        "created_at": "2026-04-19T12:14:00+00:00",
    },
    "audit_log": {
        "id": AUDIT_LOG_ID,
        "actor_id": ACTOR_ID,
        "entity_type": "task",
        "entity_id": TASK_ID,
        "action": "created",
        "payload": {"state": "in_progress"},
        "created_at": "2026-04-19T12:15:00+00:00",
    },
}


@dataclass(frozen=True)
class EntityCase:
    schema_cls: type[CreatedSchema]
    create_fn: Callable[..., CreatedSchema]
    get_fn: Callable[..., CreatedSchema | None]
    dependencies: tuple[str, ...]


ENTITY_CASES: dict[str, EntityCase] = {
    "workspace": EntityCase(WorkspaceModel, create_workspace, get_workspace, ()),
    "actor": EntityCase(ActorModel, create_actor, get_actor, ()),
    "project": EntityCase(ProjectModel, create_project, get_project, ("workspace",)),
    "task": EntityCase(TaskModel, create_task, get_task, ("project",)),
    "run": EntityCase(RunModel, create_run, get_run, ("task", "actor")),
    "artifact": EntityCase(
        ArtifactModel,
        create_artifact,
        get_artifact,
        ("task", "run"),
    ),
    "decision": EntityCase(
        DecisionModel,
        create_decision,
        get_decision,
        ("task", "run", "actor"),
    ),
    "capability": EntityCase(
        CapabilityModel,
        create_capability,
        get_capability,
        ("actor",),
    ),
    "policy": EntityCase(PolicyModel, create_policy, get_policy, ("workspace",)),
    "learning": EntityCase(
        LearningModel,
        create_learning,
        get_learning,
        ("task", "actor"),
    ),
    "packet_version": EntityCase(
        PacketVersionModel,
        create_packet_version,
        get_packet_version,
        ("task",),
    ),
    "audit_log": EntityCase(
        AuditLogModel,
        create_audit_log,
        get_audit_log,
        ("actor", "task"),
    ),
}


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def load_payload(name: str) -> CreatedSchema:
    case = ENTITY_CASES[name]
    return case.schema_cls.model_validate_json(json.dumps(ENTITY_FIXTURES[name]))


def ensure_seeded(session: Session, name: str) -> None:
    case = ENTITY_CASES[name]
    payload = load_payload(name)
    for dependency in case.dependencies:
        ensure_seeded(session, dependency)
    if case.get_fn(session, payload.id) is None:
        case.create_fn(session, payload)


@pytest.mark.parametrize("entity_name", list(ENTITY_CASES))
def test_entity_model_round_trip_and_repo_crud(
    db_session: Session,
    entity_name: str,
) -> None:
    case = ENTITY_CASES[entity_name]
    payload = load_payload(entity_name)

    for dependency in case.dependencies:
        ensure_seeded(db_session, dependency)

    assert case.schema_cls.model_validate_json(payload.model_dump_json()) == payload

    created = case.create_fn(db_session, payload)
    loaded = case.get_fn(db_session, payload.id)

    assert created == payload
    assert loaded == payload
