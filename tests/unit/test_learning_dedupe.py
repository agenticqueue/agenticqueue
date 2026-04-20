from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.learnings import (
    ConfirmLearningDraftRequest,
    DedupeSuggestion,
    DraftLearningRecord,
    DraftStore,
    MergeDecision,
    build_dedupe_text,
)
from agenticqueue_api.models import ActorModel, LearningModel
from agenticqueue_api.models.edge import EdgeRelation
from agenticqueue_api.models.learning import LearningRecord
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
from agenticqueue_api.schemas.learning import LearningConfidence


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _example_contract() -> dict[str, Any]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _submission(evidence_uri: str) -> dict[str, Any]:
    contract = _example_contract()
    output = copy.deepcopy(contract["output"])
    output["artifacts"][0]["uri"] = evidence_uri
    return {
        "output": output,
        "dod_results": [
            {"item": contract["dod_checklist"][0], "checked": True},
            {"item": contract["dod_checklist"][1], "checked": True},
        ],
        "had_failure": False,
        "had_block": False,
        "had_retry": False,
    }


def _make_actor_payload(*, handle: str) -> ActorModel:
    return ActorModel.model_validate(
        {
            "id": str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"https://agenticqueue.ai/unit-tests/{handle}",
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


def _seed_task_run(
    session: Session, *, handle: str
) -> tuple[uuid.UUID, TaskModel, RunModel]:
    actor = create_actor(session, _make_actor_payload(handle=handle))
    workspace = create_workspace(
        session,
        WorkspaceModel.model_validate(
            {
                "id": str(uuid.uuid4()),
                "slug": f"{handle}-workspace",
                "name": f"{handle.title()} Workspace",
                "description": "Workspace for learning dedupe tests",
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
                "description": "Project for learning dedupe tests",
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
                "title": "Confirm dedupe learning draft",
                "state": "done",
                "description": "Unit test for learning dedupe.",
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
                "summary": "Learning dedupe unit test",
                "details": {},
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:10:00+00:00",
            }
        ),
    )
    return actor.id, task, run


def _learning_payload(
    *,
    suffix: int,
    task_id: uuid.UUID | None,
    title: str,
    action_rule: str,
    evidence: list[str],
    confidence: str = "tentative",
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
            "evidence": evidence,
            "scope": "task",
            "confidence": confidence,
            "status": "active",
            "review_date": "2026-05-04",
            "embedding": None,
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )


def _seed_pending_draft(
    session: Session,
    *,
    handle: str,
    title: str,
    action_rule: str,
    evidence: list[str],
    confidence: str = "tentative",
) -> tuple[uuid.UUID, uuid.UUID]:
    actor_id, task, run = _seed_task_run(session, handle=handle)
    record = DraftLearningRecord(
        task_id=task.id,
        run_id=run.id,
        payload={
            "title": title,
            "type": "pattern",
            "what_happened": "A reusable learning was captured.",
            "what_learned": "The pattern should be reused later.",
            "action_rule": action_rule,
            "applies_when": "The same integration path appears again.",
            "does_not_apply_when": "The dependency graph changed materially.",
            "evidence": evidence,
            "scope": "task",
            "confidence": confidence,
            "status": "active",
            "owner": "agenticqueue-auto-draft",
            "review_date": "2026-05-04",
        },
        draft_status="pending",
    )
    session.add(record)
    session.flush()
    return actor_id, record.id


def _embedder(mapping: dict[str, list[float]]):
    def embed_text(text: str) -> list[float]:
        return mapping[text]

    return embed_text


def _vector(index: int) -> list[float]:
    vector = [0.0] * 768
    vector[index] = 1.0
    return vector


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
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def test_confirm_returns_suggestion_for_near_duplicate(db_session: Session) -> None:
    title = f"Capture validator retry pattern {uuid.uuid4()}"
    action_rule = "Fix the validator payload before retrying the run."
    actor_id, draft_id = _seed_pending_draft(
        db_session,
        handle="suggest",
        title=title,
        action_rule=action_rule,
        evidence=["artifact://draft"],
    )
    existing = create_learning(
        db_session,
        _learning_payload(
            suffix=701,
            task_id=None,
            title=title,
            action_rule=action_rule,
            evidence=["artifact://existing"],
        ),
    )
    store = DraftStore(db_session)
    embed_text = _embedder({build_dedupe_text(title, action_rule): _vector(0)})

    result = store.confirm(draft_id, owner_actor_id=actor_id, embed_text=embed_text)

    assert isinstance(result, DedupeSuggestion)
    assert result.matched_learning.id == existing.id
    assert result.similarity == pytest.approx(1.0)
    persisted = db_session.get(DraftLearningRecord, draft_id)
    assert persisted is not None
    assert persisted.draft_status == "pending"


def test_accept_merge_keeps_one_learning_and_promotes_confidence(
    db_session: Session,
) -> None:
    title = f"Capture validator retry pattern {uuid.uuid4()}"
    action_rule = "Fix the validator payload before retrying the run."
    existing = create_learning(
        db_session,
        _learning_payload(
            suffix=711,
            task_id=None,
            title=title,
            action_rule=action_rule,
            evidence=["artifact://existing"],
        ),
    )
    embed_text = _embedder({build_dedupe_text(title, action_rule): _vector(0)})
    store = DraftStore(db_session)

    actor_id, first_draft_id = _seed_pending_draft(
        db_session,
        handle="accept-one",
        title=title,
        action_rule=action_rule,
        evidence=["artifact://draft-1"],
    )
    store.confirm(
        first_draft_id,
        owner_actor_id=actor_id,
        request=ConfirmLearningDraftRequest(
            merge_decision=MergeDecision.ACCEPT,
            matched_learning_id=existing.id,
        ),
        embed_text=embed_text,
    )

    actor_id, second_draft_id = _seed_pending_draft(
        db_session,
        handle="accept-two",
        title=title,
        action_rule=action_rule,
        evidence=["artifact://draft-2"],
        confidence=LearningConfidence.CONFIRMED.value,
    )
    result = store.confirm(
        second_draft_id,
        owner_actor_id=actor_id,
        request=ConfirmLearningDraftRequest(
            merge_decision=MergeDecision.ACCEPT,
            matched_learning_id=existing.id,
        ),
        embed_text=embed_text,
    )

    assert not isinstance(result, DedupeSuggestion)
    assert result.learning.id == existing.id
    assert result.learning.evidence == [
        "artifact://existing",
        "artifact://draft-1",
        "artifact://draft-2",
    ]
    assert result.learning.confidence == LearningConfidence.VALIDATED.value
    matching_ids = set(
        db_session.scalars(
            sa.select(LearningRecord.id).where(
                LearningRecord.title == title,
                LearningRecord.action_rule == action_rule,
            )
        )
    )
    assert matching_ids == {existing.id}


def test_reject_merge_creates_related_edge(db_session: Session) -> None:
    title = f"Capture validator retry pattern {uuid.uuid4()}"
    action_rule = "Fix the validator payload before retrying the run."
    actor_id, draft_id = _seed_pending_draft(
        db_session,
        handle="reject",
        title=title,
        action_rule=action_rule,
        evidence=["artifact://draft"],
    )
    existing = create_learning(
        db_session,
        _learning_payload(
            suffix=721,
            task_id=None,
            title=title,
            action_rule=action_rule,
            evidence=["artifact://existing"],
        ),
    )
    store = DraftStore(db_session)
    embed_text = _embedder({build_dedupe_text(title, action_rule): _vector(0)})

    result = store.confirm(
        draft_id,
        owner_actor_id=actor_id,
        request=ConfirmLearningDraftRequest(
            merge_decision=MergeDecision.REJECT,
            matched_learning_id=existing.id,
        ),
        embed_text=embed_text,
    )

    assert not isinstance(result, DedupeSuggestion)
    related = neighbors(
        db_session,
        "learning",
        result.learning.id,
        edge_types=(EdgeRelation.RELATED_TO,),
    )
    assert [hit.entity_id for hit in related] == [existing.id]


def test_confirm_without_match_creates_new_learning(db_session: Session) -> None:
    existing_title = f"Capture validator retry pattern {uuid.uuid4()}"
    existing_rule = "Fix the validator payload before retrying the run."
    new_title = f"Document graph traversal remediation {uuid.uuid4()}"
    new_rule = "Use bounded depth traversal when decision edges fan out."
    actor_id, draft_id = _seed_pending_draft(
        db_session,
        handle="no-match",
        title=new_title,
        action_rule=new_rule,
        evidence=["artifact://draft"],
    )
    existing = create_learning(
        db_session,
        _learning_payload(
            suffix=731,
            task_id=None,
            title=existing_title,
            action_rule=existing_rule,
            evidence=["artifact://existing"],
        ),
    )
    store = DraftStore(db_session)
    embed_text = _embedder(
        {
            build_dedupe_text(existing_title, existing_rule): _vector(0),
            build_dedupe_text(new_title, new_rule): _vector(1),
        }
    )

    result = store.confirm(draft_id, owner_actor_id=actor_id, embed_text=embed_text)

    assert not isinstance(result, DedupeSuggestion)
    assert result.learning.id != existing.id
    assert result.learning.evidence == ["artifact://draft"]
