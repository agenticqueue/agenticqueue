from __future__ import annotations

import copy
import json
from pathlib import Path
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.learnings import (
    ConfirmedDraftLearningView,
    DraftStore,
    LearningLifecycleService,
    LearningPromotionService,
)
from agenticqueue_api.models import ActorModel, CapabilityKey, CapabilityRecord
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
)
from tests.helpers.stub_packet import compile_packet

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


def _example_contract(
    surface_area: list[str],
    file_scope: list[str],
    spec: str,
) -> dict[str, Any]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["surface_area"] = surface_area
    contract["file_scope"] = file_scope
    contract["spec"] = spec
    return contract


def _deterministic_uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{label}")


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
def clean_database(engine: Engine) -> None:
    truncate_all_tables(engine)


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def _create_actor(session: Session, *, handle: str) -> ActorModel:
    return create_actor(
        session,
        ActorModel.model_validate(
            {
                "id": str(_deterministic_uuid(f"actor-{handle}")),
                "handle": handle,
                "actor_type": "agent",
                "display_name": handle.replace("-", " ").title(),
                "auth_subject": f"{handle}-subject",
                "is_active": True,
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
            }
        ),
    )


def _create_workspace(session: Session, *, slug: str) -> WorkspaceModel:
    return create_workspace(
        session,
        WorkspaceModel.model_validate(
            {
                "id": str(_deterministic_uuid(f"workspace-{slug}")),
                "slug": slug,
                "name": slug.replace("-", " ").title(),
                "description": "Workspace for learnings loop tests",
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
            }
        ),
    )


def _create_project(
    session: Session,
    *,
    workspace_id: uuid.UUID,
    slug: str,
) -> ProjectModel:
    return create_project(
        session,
        ProjectModel.model_validate(
            {
                "id": str(_deterministic_uuid(f"project-{slug}")),
                "workspace_id": str(workspace_id),
                "slug": slug,
                "name": slug.replace("-", " ").title(),
                "description": "Project for learnings loop tests",
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
            }
        ),
    )


def _create_task(
    session: Session,
    *,
    project_id: uuid.UUID,
    label: str,
    title: str,
    spec: str,
) -> TaskModel:
    return create_task(
        session,
        TaskModel.model_validate(
            {
                "id": str(_deterministic_uuid(f"task-{label}")),
                "project_id": str(project_id),
                "task_type": "coding-task",
                "title": title,
                "state": "done",
                "description": spec,
                "contract": _example_contract(
                    ["learnings", "packet", "tests"],
                    [
                        "tests/helpers/stub_packet.py",
                        "tests/e2e/test_learnings_loop.py",
                    ],
                    spec,
                ),
                "definition_of_done": ["tests pass", "packet includes learnings"],
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
            }
        ),
    )


def _create_run(
    session: Session,
    *,
    task_id: uuid.UUID,
    actor_id: uuid.UUID,
    label: str,
    details: dict[str, Any],
) -> RunModel:
    return create_run(
        session,
        RunModel.model_validate(
            {
                "id": str(_deterministic_uuid(f"run-{label}")),
                "task_id": str(task_id),
                "actor_id": str(actor_id),
                "status": "completed",
                "started_at": "2026-04-20T00:00:00+00:00",
                "ended_at": "2026-04-20T00:10:00+00:00",
                "summary": "Learnings loop closeout",
                "details": details,
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:10:00+00:00",
            }
        ),
    )


def _create_learning(
    session: Session,
    *,
    task_id: uuid.UUID,
    actor_id: uuid.UUID,
    label: str,
    title: str,
    action_rule: str,
    scope: str,
    status: str = "active",
    what_happened: str | None = None,
    what_learned: str | None = None,
) -> LearningModel:
    return create_learning(
        session,
        LearningModel.model_validate(
            {
                "id": str(_deterministic_uuid(f"learning-{label}")),
                "task_id": str(task_id),
                "owner_actor_id": str(actor_id),
                "owner": "learnings-loop-agent",
                "title": title,
                "learning_type": "pattern",
                "what_happened": what_happened
                or "The same learning-loop issue surfaced during execution.",
                "what_learned": what_learned
                or "The learnings loop needs one reusable response.",
                "action_rule": action_rule,
                "applies_when": "A coding-task packet should inherit prior learnings.",
                "does_not_apply_when": "The task has no reusable prior context.",
                "evidence": [f"artifact://{label}"],
                "scope": scope,
                "confidence": "confirmed",
                "status": status,
                "review_date": "2026-05-20",
                "embedding": None,
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
            }
        ),
    )


def _successful_submission() -> dict[str, Any]:
    contract = _example_contract(
        ["learnings", "packet", "tests"],
        ["tests/helpers/stub_packet.py"],
        "Close the learnings loop on the test harness.",
    )
    return {
        "output": copy.deepcopy(contract["output"]),
        "dod_results": [
            {"item": "tests pass", "checked": True},
            {"item": "packet includes learnings", "checked": True},
        ],
        "had_failure": False,
        "had_block": False,
        "had_retry": False,
    }


def test_confirmed_closeout_learning_surfaces_in_the_next_stub_packet(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        actor = _create_actor(session, handle="loop-closeout")
        workspace = _create_workspace(session, slug="loop-workspace")
        project = _create_project(
            session,
            workspace_id=workspace.id,
            slug="loop-project",
        )
        task_a = _create_task(
            session,
            project_id=project.id,
            label="loop-a",
            title="Close out a validator-heavy learning task",
            spec="Draft and confirm a learning after validator rejection.",
        )
        run_a = _create_run(
            session,
            task_id=task_a.id,
            actor_id=actor.id,
            label="loop-a",
            details={
                "retry_count": 0,
                "attempts": [
                    {
                        "status": "rejected",
                        "error_source": "validator",
                        "validator_errors": [
                            {
                                "field": "output.diff_url",
                                "message": "Field required",
                            }
                        ],
                    },
                    {
                        "status": "succeeded",
                        "summary": "Submission accepted",
                    },
                ],
            },
        )
        drafts = DraftStore(session).create_drafts(
            task=task_a,
            run=run_a,
            submission=_successful_submission(),
        )
        assert len(drafts) == 1

        confirmed = DraftStore(session).confirm(
            drafts[0].id,
            owner_actor_id=actor.id,
        )
        assert isinstance(confirmed, ConfirmedDraftLearningView)

        task_b = _create_task(
            session,
            project_id=project.id,
            label="loop-b",
            title="Compile the next packet with prior learnings",
            spec="Pull the confirmed closeout learning into the next packet.",
        )
        packet = compile_packet(session, task_b.id)

        assert packet["task"]["id"] == str(task_b.id)
        assert [item["title"] for item in packet["relevant_learnings"]] == [
            confirmed.learning.title
        ]


def test_three_project_repeat_marks_a_project_learning_global_eligible(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        actor = _create_actor(session, handle="loop-promotion")
        workspace = _create_workspace(session, slug="promotion-workspace")
        title = "Promote the learnings loop once the same issue spans projects"
        action_rule = "Promote repeated project learnings when the same issue lands in multiple projects."

        for suffix in ("one", "two", "three"):
            project = _create_project(
                session,
                workspace_id=workspace.id,
                slug=f"promotion-{suffix}",
            )
            task = _create_task(
                session,
                project_id=project.id,
                label=f"promotion-{suffix}",
                title=f"Project {suffix} repeats the same learnings issue",
                spec="Track the same learning signature across multiple projects.",
            )
            _create_learning(
                session,
                task_id=task.id,
                actor_id=actor.id,
                label=f"promotion-{suffix}",
                title=title,
                action_rule=action_rule,
                scope="project",
            )

        target_task = _create_task(
            session,
            project_id=_deterministic_uuid("project-promotion-three"),
            label="promotion-followup",
            title="Inspect promotion eligibility in the next packet",
            spec="Surface promotion-eligible learnings in a stub packet.",
        )

        candidates = LearningPromotionService(session).auto_promote_candidates()
        assert [candidate.title for candidate in candidates] == [title]
        assert candidates[0].promotion_eligible is True

        packet = compile_packet(session, target_task.id)
        assert any(
            item["title"] == title and item["promotion_eligible"]
            for item in packet["relevant_learnings"]
        )


def test_superseded_learning_disappears_from_stub_packets(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        actor = _create_actor(session, handle="loop-supersede")
        workspace = _create_workspace(session, slug="supersede-workspace")
        project = _create_project(
            session,
            workspace_id=workspace.id,
            slug="supersede-project",
        )
        old_task = _create_task(
            session,
            project_id=project.id,
            label="supersede-old-task",
            title="Carry an outdated learnings workaround",
            spec="Legacy workaround task.",
        )
        replacement_task = _create_task(
            session,
            project_id=project.id,
            label="supersede-new-task",
            title="Replace the workaround with the shared learnings loop",
            spec="Replacement task.",
        )
        followup_task = _create_task(
            session,
            project_id=project.id,
            label="supersede-followup-task",
            title="Compile a packet after supersession",
            spec="The packet should ignore superseded learnings.",
        )
        old_learning = _create_learning(
            session,
            task_id=old_task.id,
            actor_id=actor.id,
            label="supersede-old",
            title="Patch each learning transport by hand",
            action_rule="Apply one-off fixes per transport when a learning changes.",
            scope="project",
        )
        replacement_learning = _create_learning(
            session,
            task_id=replacement_task.id,
            actor_id=actor.id,
            label="supersede-new",
            title="Route every learning change through the shared surface",
            action_rule="Use the shared learnings surface instead of transport-specific patches.",
            scope="project",
        )

        updated = LearningLifecycleService(session).supersede(
            old_learning_id=old_learning.id,
            new_learning_id=replacement_learning.id,
            reason="Shared learning surface replaced the workaround.",
            created_by=actor.id,
        )
        assert updated.status == "superseded"

        packet = compile_packet(session, followup_task.id)
        titles = [item["title"] for item in packet["relevant_learnings"]]

        assert "Patch each learning transport by hand" not in titles
        assert "Route every learning change through the shared surface" in titles
