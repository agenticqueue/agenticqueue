from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.capabilities import grant_capability
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.learnings.draft import DraftLearningRecord, DraftStore
from agenticqueue_api.models import ActorModel, CapabilityKey, CapabilityRecord
from agenticqueue_api.models.project import ProjectModel
from agenticqueue_api.models.run import RunModel
from agenticqueue_api.models.task import TaskModel
from agenticqueue_api.models.workspace import WorkspaceModel
from agenticqueue_api.repo import (
    create_actor,
    create_project,
    create_run,
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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _example_contract() -> dict[str, Any]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _submission() -> dict[str, Any]:
    contract = _example_contract()
    return {
        "output": copy.deepcopy(contract["output"]),
        "dod_results": [
            {"item": contract["dod_checklist"][0], "checked": True},
            {"item": contract["dod_checklist"][1], "checked": True},
        ],
        "had_failure": False,
        "had_block": False,
        "had_retry": True,
    }


def _draft_run_details() -> dict[str, Any]:
    return {
        "retry_count": 2,
        "attempts": [
            {
                "status": "rejected",
                "error_source": "validator",
                "validator_errors": [
                    {"field": "output.diff_url", "message": "Field required"}
                ],
            },
            {
                "status": "rejected",
                "error_source": "validator",
                "validator_errors": [
                    {
                        "field": "output.learnings.0.title",
                        "message": "Field required",
                    }
                ],
            },
            {"status": "succeeded", "summary": "Submission accepted"},
        ],
    }


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


def make_actor_payload(*, handle: str) -> ActorModel:
    return ActorModel.model_validate(
        {
            "id": str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"https://agenticqueue.ai/tests/{handle}",
                )
            ),
            "handle": handle,
            "actor_type": "agent",
            "display_name": handle.replace("-", " ").title(),
            "auth_subject": f"{handle}-subject",
            "is_active": True,
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> None:
    truncate_all_tables(engine)


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(create_app(session_factory=session_factory)) as test_client:
        yield test_client


def _seed_task_run_and_token(
    session_factory: sessionmaker[Session],
    *,
    handle: str,
) -> tuple[uuid.UUID, str, TaskModel, RunModel]:
    with session_factory() as session:
        actor = create_actor(session, make_actor_payload(handle=handle))
        workspace = create_workspace(
            session,
            WorkspaceModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "slug": f"{handle}-workspace",
                    "name": f"{handle.title()} Workspace",
                    "description": "Workspace for learning draft API tests",
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
                    "slug": f"{handle}-project",
                    "name": f"{handle.title()} Project",
                    "description": "Project for learning draft API tests",
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
                    "title": "Confirm persisted learning draft",
                    "state": "done",
                    "description": "Persist and confirm a deterministic learning draft.",
                    "contract": _example_contract(),
                    "definition_of_done": _example_contract()["dod_checklist"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        run = create_run(
            session,
            RunModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "task_id": str(task.id),
                    "actor_id": str(actor.id),
                    "status": "completed",
                    "started_at": "2026-04-20T00:00:00+00:00",
                    "ended_at": "2026-04-20T00:10:00+00:00",
                    "summary": "Learning draft API integration",
                    "details": _draft_run_details(),
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:10:00+00:00",
                }
            ),
        )
        grant_capability(
            session,
            actor_id=actor.id,
            capability=CapabilityKey.WRITE_LEARNING,
            scope={"project_id": str(project.id)},
            granted_by_actor_id=actor.id,
        )
        _, token = issue_api_token(
            session,
            actor_id=actor.id,
            scopes=["learning:read", "learning:write"],
            expires_at=None,
        )
        session.commit()
        return actor.id, token, task, run


def _seed_pending_draft(
    session_factory: sessionmaker[Session],
    *,
    handle: str,
) -> tuple[uuid.UUID, str, uuid.UUID]:
    actor_id, token, task, run = _seed_task_run_and_token(
        session_factory,
        handle=handle,
    )
    with session_factory() as session:
        store = DraftStore(session)
        draft = store.create_drafts(
            task=task,
            run=run,
            submission=_submission(),
        )[0]
        session.commit()
        return actor_id, token, draft.id


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _post_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": str(uuid.uuid4()),
    }


def test_learning_draft_edit_confirm_flow_promotes_active_learning(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    actor_id, token, draft_id = _seed_pending_draft(
        session_factory,
        handle="learning-draft-editor",
    )

    edit_response = client.post(
        f"/v1/learnings/drafts/{draft_id}/edit",
        headers=_post_headers(token),
        json={"title": "Capture validator retry pattern before the next handoff"},
    )
    assert edit_response.status_code == 200
    edited = edit_response.json()
    assert edited["draft_status"] == "pending"
    assert edited["draft"]["title"] == (
        "Capture validator retry pattern before the next handoff"
    )

    confirm_response = client.post(
        f"/v1/learnings/drafts/{draft_id}/confirm",
        headers=_post_headers(token),
    )
    assert confirm_response.status_code == 200
    confirmed = confirm_response.json()
    assert confirmed["draft"]["draft_status"] == "confirmed"
    learning_id = confirmed["learning"]["id"]

    learning_response = client.get(
        f"/v1/learnings/{learning_id}",
        headers=_headers(token),
    )
    assert learning_response.status_code == 200
    learning = learning_response.json()
    assert learning["status"] == "active"
    assert learning["owner"] == "agenticqueue-auto-draft"
    assert learning["owner_actor_id"] == str(actor_id)
    assert learning["title"] == (
        "Capture validator retry pattern before the next handoff"
    )


def test_reject_requires_reason_and_confirm_rejects_invalid_payload(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _, token, draft_id = _seed_pending_draft(
        session_factory,
        handle="learning-draft-validator",
    )
    reject_response = client.post(
        f"/v1/learnings/drafts/{draft_id}/reject",
        headers=_post_headers(token),
        json={"reason": ""},
    )
    assert reject_response.status_code == 422

    _, invalid_token, task, run = _seed_task_run_and_token(
        session_factory,
        handle="learning-draft-invalid",
    )
    with session_factory() as session:
        invalid_record = DraftLearningRecord(
            task_id=task.id,
            run_id=run.id,
            payload={
                "type": "pitfall",
                "what_happened": "Validator feedback repeated twice.",
                "what_learned": "The payload shape has to be grounded earlier.",
                "action_rule": "Fix the payload before retrying.",
                "applies_when": "Validator failures repeat.",
                "does_not_apply_when": "The first submission passes.",
                "evidence": ["run://invalid"],
                "scope": "task",
                "confidence": "tentative",
                "status": "active",
                "owner": "agenticqueue-auto-draft",
                "review_date": "2026-05-04",
            },
            draft_status="pending",
        )
        session.add(invalid_record)
        session.commit()
        invalid_draft_id = invalid_record.id

    confirm_response = client.post(
        f"/v1/learnings/drafts/{invalid_draft_id}/confirm",
        headers=_post_headers(invalid_token),
    )
    assert confirm_response.status_code == 422
