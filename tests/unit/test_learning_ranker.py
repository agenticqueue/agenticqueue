from __future__ import annotations

import json
from pathlib import Path
import uuid
from typing import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.learnings import rank_learnings_for_task
from agenticqueue_api.models import (
    ActorModel,
    DecisionModel,
    EdgeModel,
    EdgeRelation,
    LearningModel,
    ProjectModel,
    RunModel,
    TaskModel,
    WorkspaceModel,
)
from agenticqueue_api.repo import (
    create_actor,
    create_decision,
    create_edge,
    create_learning,
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


def _example_contract() -> dict[str, object]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _make_actor_payload(*, handle: str) -> ActorModel:
    actor_id = uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{handle}")
    return ActorModel.model_validate(
        {
            "id": str(actor_id),
            "handle": handle,
            "actor_type": "agent",
            "display_name": handle.replace("-", " ").title(),
            "auth_subject": f"{handle}-subject",
            "is_active": True,
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )


def _task_payload(
    *,
    task_id: str,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    title: str,
    created_at: str,
    task_type: str = "coding-task",
    file_scope: list[str] | None = None,
    surface_area: list[str] | None = None,
    spec: str | None = None,
) -> tuple[TaskModel, RunModel]:
    contract = _example_contract()
    contract["file_scope"] = file_scope or []
    contract["surface_area"] = surface_area or []
    contract["spec"] = spec or ""
    task = TaskModel.model_validate(
        {
            "id": task_id,
            "project_id": str(project_id),
            "task_type": task_type,
            "title": title,
            "state": "done",
            "description": spec or title,
            "contract": contract,
            "definition_of_done": contract["dod_checklist"],
            "created_at": created_at,
            "updated_at": created_at,
        }
    )
    run = RunModel.model_validate(
        {
            "id": str(uuid.uuid4()),
            "task_id": task_id,
            "actor_id": str(actor_id),
            "status": "completed",
            "started_at": created_at,
            "ended_at": created_at,
            "summary": title,
            "details": {},
            "created_at": created_at,
            "updated_at": created_at,
        }
    )
    return task, run


def _learning_payload(
    *,
    learning_id: str,
    task_id: str | None,
    title: str,
    action_rule: str,
    scope: str,
    created_at: str,
    evidence: list[str],
) -> LearningModel:
    return LearningModel.model_validate(
        {
            "id": learning_id,
            "task_id": task_id,
            "owner_actor_id": None,
            "owner": "agenticqueue-auto-draft",
            "title": title,
            "learning_type": "pattern",
            "what_happened": "The task produced a reusable learning.",
            "what_learned": "The ranker should carry this into the next packet.",
            "action_rule": action_rule,
            "applies_when": "A similar coding task appears.",
            "does_not_apply_when": "The task changes toolchains or repo scope.",
            "evidence": evidence,
            "scope": scope,
            "confidence": "confirmed",
            "status": "active",
            "review_date": "2026-05-04",
            "embedding": None,
            "created_at": created_at,
            "updated_at": created_at,
        }
    )


def _edge_payload(
    *,
    edge_id: str,
    src_entity_type: str,
    src_id: uuid.UUID,
    dst_entity_type: str,
    dst_id: uuid.UUID,
    relation: EdgeRelation,
) -> EdgeModel:
    return EdgeModel.model_validate(
        {
            "id": edge_id,
            "src_entity_type": src_entity_type,
            "src_id": str(src_id),
            "dst_entity_type": dst_entity_type,
            "dst_id": str(dst_id),
            "relation": relation.value,
            "metadata": {},
            "created_by": None,
            "created_at": "2026-04-20T00:00:00+00:00",
        }
    )


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    qualified_tables = ", ".join(
        f"agenticqueue.{table_name}" for table_name in TRUNCATE_TABLES
    )
    connection.execute(
        sa.text(f"TRUNCATE TABLE {qualified_tables} RESTART IDENTITY CASCADE")
    )
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def test_rank_learnings_for_task_returns_diverse_golden_top_five(
    db_session: Session,
) -> None:
    actor = create_actor(db_session, _make_actor_payload(handle="ranker"))

    workspace = create_workspace(
        db_session,
        WorkspaceModel.model_validate(
            {
                "id": str(uuid.uuid4()),
                "slug": "ranker-workspace",
                "name": "Ranker Workspace",
                "description": "Workspace for learning ranker tests",
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
            }
        ),
    )
    project_a = create_project(
        db_session,
        ProjectModel.model_validate(
            {
                "id": str(uuid.uuid4()),
                "workspace_id": str(workspace.id),
                "slug": "project-a",
                "name": "Project A",
                "description": "Primary project",
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
            }
        ),
    )
    project_b = create_project(
        db_session,
        ProjectModel.model_validate(
            {
                "id": str(uuid.uuid4()),
                "workspace_id": str(workspace.id),
                "slug": "project-b",
                "name": "Project B",
                "description": "Secondary project",
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
            }
        ),
    )

    target_task, target_run = _task_payload(
        task_id=str(uuid.uuid4()),
        project_id=project_a.id,
        actor_id=actor.id,
        title="Rank learnings for the packet compiler",
        created_at="2026-04-20T00:00:00+00:00",
        file_scope=[
            "apps/api/src/agenticqueue_api/learnings/ranker.py",
            "tests/unit/test_learning_ranker.py",
        ],
        surface_area=["learnings", "pytest", "packet"],
        spec=(
            "Rank the most relevant learnings for a coding-task packet and verify "
            "the top five with pytest."
        ),
    )
    target_task = create_task(db_session, target_task)
    create_run(db_session, target_run)

    dependency_task, dependency_run = _task_payload(
        task_id=str(uuid.uuid4()),
        project_id=project_a.id,
        actor_id=actor.id,
        title="Shared learning dependency",
        created_at="2026-04-19T23:55:00+00:00",
        spec="Shared dependency for learning tasks.",
    )
    dependency_task = create_task(db_session, dependency_task)
    create_run(db_session, dependency_run)
    create_edge(
        db_session,
        _edge_payload(
            edge_id=str(uuid.uuid4()),
            src_entity_type="task",
            src_id=target_task.id,
            dst_entity_type="task",
            dst_id=dependency_task.id,
            relation=EdgeRelation.DEPENDS_ON,
        ),
    )

    decision = create_decision(
        db_session,
        DecisionModel.model_validate(
            {
                "id": str(uuid.uuid4()),
                "task_id": str(target_task.id),
                "run_id": str(target_run.id),
                "actor_id": str(actor.id),
                "summary": "Packet compiler should inject ranked learnings",
                "rationale": "The packet needs a deterministic top-five list.",
                "decided_at": "2026-04-20T00:01:00+00:00",
                "embedding": None,
                "created_at": "2026-04-20T00:01:00+00:00",
            }
        ),
    )

    def make_source_task(
        *,
        project_id: uuid.UUID,
        created_at: str,
        title: str,
        task_type: str = "coding-task",
        file_scope: list[str] | None = None,
        surface_area: list[str] | None = None,
        spec: str | None = None,
        shared_dependency: bool = False,
    ) -> TaskModel:
        task, run = _task_payload(
            task_id=str(uuid.uuid4()),
            project_id=project_id,
            actor_id=actor.id,
            title=title,
            created_at=created_at,
            task_type=task_type,
            file_scope=file_scope,
            surface_area=surface_area,
            spec=spec,
        )
        task = create_task(db_session, task)
        create_run(db_session, run)
        if shared_dependency:
            create_edge(
                db_session,
                _edge_payload(
                    edge_id=str(uuid.uuid4()),
                    src_entity_type="task",
                    src_id=task.id,
                    dst_entity_type="task",
                    dst_id=dependency_task.id,
                    relation=EdgeRelation.DEPENDS_ON,
                ),
            )
        return task

    source_a = make_source_task(
        project_id=project_a.id,
        created_at="2026-04-19T23:40:00+00:00",
        title="Score learnings for packet compiler retries",
        file_scope=[
            "apps/api/src/agenticqueue_api/learnings/ranker.py",
            "tests/unit/test_learning_ranker.py",
        ],
        surface_area=["learnings", "pytest", "packet"],
        spec="Use pytest to verify the packet compiler chooses the best learning.",
        shared_dependency=True,
    )
    source_b = make_source_task(
        project_id=project_a.id,
        created_at="2026-04-19T23:42:00+00:00",
        title="Score learnings for packet compiler retries duplicate",
        file_scope=[
            "apps/api/src/agenticqueue_api/learnings/ranker.py",
            "tests/unit/test_learning_ranker.py",
        ],
        surface_area=["learnings", "pytest", "packet"],
        spec="Use pytest to verify the packet compiler chooses the best learning.",
        shared_dependency=True,
    )
    source_c = make_source_task(
        project_id=project_a.id,
        created_at="2026-04-19T23:44:00+00:00",
        title="Promote learnings after ranking",
        file_scope=[
            "apps/api/src/agenticqueue_api/learnings/promotion.py",
            "apps/api/src/agenticqueue_api/learnings/ranker.py",
        ],
        surface_area=["learnings", "packet"],
        spec="Feed the ranked learnings into packet assembly after scoring.",
        shared_dependency=True,
    )
    source_d = make_source_task(
        project_id=project_b.id,
        created_at="2026-04-19T23:46:00+00:00",
        title="Reuse pytest evidence for learning ranker",
        file_scope=[
            "apps/api/src/agenticqueue_api/learnings/ranker.py",
            "tests/unit/test_learning_ranker.py",
        ],
        surface_area=["pytest", "packet"],
        spec="Use pytest evidence to confirm the learning ranker order.",
    )
    source_f = make_source_task(
        project_id=project_a.id,
        created_at="2026-04-19T23:48:00+00:00",
        title="Document packet learnings for reviewers",
        task_type="docs-task",
        file_scope=[
            "docs/learnings/packet.md",
            "tests/unit/test_learning_ranker.py",
        ],
        surface_area=["packet", "pytest"],
        spec="Explain how pytest verifies ranked packet learnings.",
        shared_dependency=True,
    )
    source_g = make_source_task(
        project_id=project_b.id,
        created_at="2026-04-19T23:50:00+00:00",
        title="Unrelated deployment hardening",
        file_scope=["docker-compose.yml"],
        surface_area=["docker"],
        spec="Ship the compose stack for deployment.",
    )

    learning_a = create_learning(
        db_session,
        _learning_payload(
            learning_id=str(uuid.uuid4()),
            task_id=str(source_a.id),
            title="Rank packet learnings with pytest-backed repo overlap",
            action_rule=(
                "Prefer learnings from the same project and coding-task type when "
                "pytest and repo scope overlap on the packet compiler."
            ),
            scope="task",
            created_at="2026-04-19T23:40:00+00:00",
            evidence=["tests/unit/test_learning_ranker.py", "artifact://packet-a"],
        ),
    )
    learning_b = create_learning(
        db_session,
        _learning_payload(
            learning_id=str(uuid.uuid4()),
            task_id=str(source_b.id),
            title="Rank packet learnings with pytest-backed repo overlap duplicate",
            action_rule=(
                "Prefer learnings from the same project and coding-task type when "
                "pytest and repo scope overlap on the packet compiler."
            ),
            scope="task",
            created_at="2026-04-19T23:42:00+00:00",
            evidence=["tests/unit/test_learning_ranker.py", "artifact://packet-b"],
        ),
    )
    learning_c = create_learning(
        db_session,
        _learning_payload(
            learning_id=str(uuid.uuid4()),
            task_id=str(source_c.id),
            title="Feed ranked learnings into the packet compiler",
            action_rule=(
                "After scoring repo overlap, keep the ranked learnings close to the "
                "packet assembler and verify the order with pytest."
            ),
            scope="task",
            created_at="2026-04-19T23:44:00+00:00",
            evidence=["apps/api/src/agenticqueue_api/learnings/promotion.py"],
        ),
    )
    learning_d = create_learning(
        db_session,
        _learning_payload(
            learning_id=str(uuid.uuid4()),
            task_id=str(source_d.id),
            title="Reuse pytest evidence when ranking packet learnings",
            action_rule=(
                "When another coding-task ranks learnings for packets, reuse pytest "
                "evidence and matching repo scope before broader heuristics."
            ),
            scope="task",
            created_at="2026-04-19T23:46:00+00:00",
            evidence=["tests/unit/test_learning_ranker.py"],
        ),
    )
    learning_e = create_learning(
        db_session,
        _learning_payload(
            learning_id=str(uuid.uuid4()),
            task_id=None,
            title="Global packet compiler ranking rule for pytest-backed learnings",
            action_rule=(
                "When a coding-task packet compiler ranks learnings, keep pytest "
                "evidence and packet-compiler repo scope near the top of the packet."
            ),
            scope="global",
            created_at="2026-04-19T23:47:00+00:00",
            evidence=[
                "tests/unit/test_learning_ranker.py",
                "packet://ranker-top-five",
            ],
        ),
    )
    learning_f = create_learning(
        db_session,
        _learning_payload(
            learning_id=str(uuid.uuid4()),
            task_id=str(source_f.id),
            title="Document how reviewers read ranked packet learnings",
            action_rule=(
                "For docs or reviewer tasks in the same project, keep the ranked "
                "packet learnings alongside the pytest explanation."
            ),
            scope="task",
            created_at="2026-04-19T23:48:00+00:00",
            evidence=["docs/learnings/packet.md"],
        ),
    )
    create_learning(
        db_session,
        _learning_payload(
            learning_id=str(uuid.uuid4()),
            task_id=str(source_g.id),
            title="Deploy the compose stack before UI work",
            action_rule="Use docker-compose health checks before shipping deployment code.",
            scope="task",
            created_at="2026-04-19T23:50:00+00:00",
            evidence=["docker-compose.yml"],
        ),
    )
    create_learning(
        db_session,
        _learning_payload(
            learning_id=str(uuid.uuid4()),
            task_id=None,
            title="Global CLI help text reminder",
            action_rule="Keep Typer help text short and focused.",
            scope="global",
            created_at="2026-04-19T23:51:00+00:00",
            evidence=["cli://help"],
        ),
    )
    create_learning(
        db_session,
        _learning_payload(
            learning_id=str(uuid.uuid4()),
            task_id=None,
            title="Security scans should keep SBOM artifacts",
            action_rule="Attach the SBOM to release jobs before tagging.",
            scope="project",
            created_at="2026-04-19T23:52:00+00:00",
            evidence=["sbom://artifact"],
        ),
    )
    create_learning(
        db_session,
        _learning_payload(
            learning_id=str(uuid.uuid4()),
            task_id=None,
            title="Analytics dashboard needs stable labels",
            action_rule="Normalize labels before rendering charts.",
            scope="project",
            created_at="2026-04-19T23:53:00+00:00",
            evidence=["analytics://labels"],
        ),
    )

    for learning in (learning_a, learning_b, learning_c):
        create_edge(
            db_session,
            _edge_payload(
                edge_id=str(uuid.uuid4()),
                src_entity_type="decision",
                src_id=decision.id,
                dst_entity_type="learning",
                dst_id=learning.id,
                relation=EdgeRelation.LEARNED_FROM,
            ),
        )

    ranked = rank_learnings_for_task(db_session, target_task.id, k=5)

    ranked_ids = [learning.id for learning in ranked]
    expected_ids = [
        learning_a.id,
        learning_c.id,
        learning_f.id,
        learning_d.id,
        learning_e.id,
    ]
    assert ranked_ids == expected_ids
    assert learning_b.id not in ranked_ids

    relevant_ids = {
        learning_a.id,
        learning_c.id,
        learning_d.id,
        learning_e.id,
        learning_f.id,
        learning_b.id,
    }
    precision_at_five = sum(1 for learning in ranked if learning.id in relevant_ids) / 5
    assert precision_at_five >= 0.8
    assert ranked[0].task_id == source_a.id
