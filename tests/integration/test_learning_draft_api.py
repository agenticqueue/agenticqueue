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
from agenticqueue_api.learnings import DraftLearningPatch
from agenticqueue_api.learnings.draft import DraftLearningRecord, DraftStore
from agenticqueue_api.models import ActorModel, CapabilityKey, CapabilityRecord
from agenticqueue_api.models.edge import EdgeRelation
from agenticqueue_api.models.learning import LearningModel
from agenticqueue_api.models.project import ProjectModel
from agenticqueue_api.models.run import RunModel
from agenticqueue_api.models.task import TaskModel
from agenticqueue_api.models.workspace import WorkspaceModel
from agenticqueue_api.repo import (
    create_actor,
    create_learning,
    create_project,
    create_run,
    create_task,
    create_workspace,
    neighbors,
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


def _normalize_response_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_response_payload(item)
            for key, item in value.items()
            if key != "id"
            and not key.endswith("_id")
            and key not in {"created_at", "updated_at"}
        }
    if isinstance(value, list):
        return [_normalize_response_payload(item) for item in value]
    if isinstance(value, str) and value.startswith("run://"):
        return "run://<redacted>"
    return value


def _create_existing_learning(
    session_factory: sessionmaker[Session],
    *,
    task_id: uuid.UUID,
    title: str,
    action_rule: str,
    evidence: list[str],
) -> uuid.UUID:
    with session_factory() as session:
        learning = create_learning(
            session,
            LearningModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "task_id": str(task_id),
                    "owner_actor_id": None,
                    "owner": "agenticqueue-auto-draft",
                    "title": title,
                    "learning_type": "pattern",
                    "what_happened": "Existing learning",
                    "what_learned": "Keep the workaround documented.",
                    "action_rule": action_rule,
                    "applies_when": "The validator path repeats.",
                    "does_not_apply_when": "The contract shape changed.",
                    "evidence": evidence,
                    "scope": "task",
                    "confidence": "tentative",
                    "status": "active",
                    "review_date": "2026-05-04",
                    "embedding": None,
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        session.commit()
        return learning.id


def _seed_pending_duplicate_draft(
    session_factory: sessionmaker[Session],
    *,
    handle: str,
    title: str,
    action_rule: str,
    evidence: list[str],
) -> tuple[uuid.UUID, str, uuid.UUID, uuid.UUID]:
    actor_id, token, task, run = _seed_task_run_and_token(
        session_factory,
        handle=handle,
    )
    existing_learning_id = _create_existing_learning(
        session_factory,
        task_id=task.id,
        title=title,
        action_rule=action_rule,
        evidence=["artifact://existing"],
    )
    with session_factory() as session:
        store = DraftStore(session)
        draft = store.create_drafts(
            task=task,
            run=run,
            submission=_submission(),
        )[0]
        store.edit(
            draft.id,
            DraftLearningPatch(
                title=title,
                action_rule=action_rule,
                evidence=evidence,
            ),
        )
        session.commit()
        return actor_id, token, draft.id, existing_learning_id


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


@pytest.mark.parametrize(
    ("suffix", "body"),
    [
        ("edit", {"title": "Keep the validator retry lesson colocated"}),
        ("reject", {"reason": "Needs a stronger action rule"}),
        ("confirm", None),
    ],
)
def test_hidden_alias_routes_match_canonical_learning_draft_responses(
    client: TestClient,
    engine: Engine,
    session_factory: sessionmaker[Session],
    suffix: str,
    body: dict[str, Any] | None,
) -> None:
    handle = f"alias-route-{suffix}"

    _, canonical_token, canonical_draft_id = _seed_pending_draft(
        session_factory,
        handle=handle,
    )

    canonical_response = client.post(
        f"/v1/learnings/drafts/{canonical_draft_id}/{suffix}",
        headers=_post_headers(canonical_token),
        json=body,
    )

    truncate_all_tables(engine)

    _, alias_token, alias_draft_id = _seed_pending_draft(
        session_factory,
        handle=handle,
    )
    alias_response = client.post(
        f"/learnings/drafts/{alias_draft_id}/{suffix}",
        headers=_post_headers(alias_token),
        json=body,
    )

    assert canonical_response.status_code == alias_response.status_code == 200
    canonical_payload = canonical_response.json()
    alias_payload = alias_response.json()

    if suffix == "edit":
        assert canonical_payload["draft_status"] == alias_payload["draft_status"]
        assert canonical_payload.get("reason") == alias_payload.get("reason")
        assert canonical_payload["draft"]["title"] == alias_payload["draft"]["title"]
        return

    if suffix == "reject":
        assert canonical_payload["draft_status"] == alias_payload["draft_status"]
        assert canonical_payload.get("reason") == alias_payload.get("reason")
        assert _normalize_response_payload(
            canonical_payload["draft"]
        ) == _normalize_response_payload(alias_payload["draft"])
        return

    assert canonical_payload["draft"]["draft_status"] == alias_payload["draft"][
        "draft_status"
    ]
    assert canonical_payload["learning"]["title"] == alias_payload["learning"]["title"]
    assert canonical_payload["learning"]["action_rule"] == alias_payload["learning"][
        "action_rule"
    ]


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


def test_confirm_returns_dedupe_suggestion_then_accepts_merge(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _, token, draft_id, existing_learning_id = _seed_pending_duplicate_draft(
        session_factory,
        handle="learning-draft-dedupe",
        title="Capture validator retry pattern",
        action_rule="Fix the validator payload before retrying the run.",
        evidence=["artifact://draft"],
    )

    suggestion_response = client.post(
        f"/v1/learnings/drafts/{draft_id}/confirm",
        headers=_post_headers(token),
    )
    assert suggestion_response.status_code == 200
    suggestion = suggestion_response.json()
    assert suggestion["matched_learning"]["id"] == str(existing_learning_id)
    assert suggestion["threshold"] == pytest.approx(0.92)

    merge_response = client.post(
        f"/v1/learnings/drafts/{draft_id}/confirm",
        headers=_post_headers(token),
        json={
            "merge_decision": "accept",
            "matched_learning_id": str(existing_learning_id),
        },
    )
    assert merge_response.status_code == 200
    confirmed = merge_response.json()
    assert confirmed["learning"]["id"] == str(existing_learning_id)
    assert confirmed["learning"]["evidence"] == [
        "artifact://existing",
        "artifact://draft",
    ]
    assert confirmed["learning"]["confidence"] == "confirmed"


def test_confirm_reject_merge_creates_related_edge(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _, token, draft_id, existing_learning_id = _seed_pending_duplicate_draft(
        session_factory,
        handle="learning-draft-related",
        title="Capture validator retry pattern",
        action_rule="Fix the validator payload before retrying the run.",
        evidence=["artifact://draft-related"],
    )

    response = client.post(
        f"/v1/learnings/drafts/{draft_id}/confirm",
        headers=_post_headers(token),
        json={
            "merge_decision": "reject",
            "matched_learning_id": str(existing_learning_id),
        },
    )
    assert response.status_code == 200
    confirmed = response.json()
    learning_id = uuid.UUID(confirmed["learning"]["id"])

    with session_factory() as session:
        related = neighbors(
            session,
            "learning",
            learning_id,
            edge_types=(EdgeRelation.RELATED_TO,),
        )
    assert [hit.entity_id for hit in related] == [existing_learning_id]
