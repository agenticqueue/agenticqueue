from __future__ import annotations

import json
from pathlib import Path
import uuid
from typing import Any, Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agenticqueue_api.compiler import packet_decisions
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import (
    ActorModel,
    DecisionModel,
    EdgeModel,
    EdgeRelation,
    ProjectModel,
    RunModel,
    TaskModel,
    WorkspaceModel,
)
from agenticqueue_api.repo import (
    create_actor,
    create_decision,
    create_edge,
    create_project,
    create_run,
    create_task,
    create_workspace,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _coding_contract(
    *,
    spec: str,
    file_scope: list[str],
    surface_area: list[str],
) -> dict[str, Any]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["spec"] = spec
    contract["file_scope"] = file_scope
    contract["surface_area"] = surface_area
    return contract


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


def _actor_payload(actor_id: uuid.UUID) -> ActorModel:
    return ActorModel.model_validate(
        {
            "id": str(actor_id),
            "handle": "packet-compiler",
            "actor_type": "agent",
            "display_name": "Packet Compiler",
            "auth_subject": "packet-compiler-subject",
            "is_active": True,
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )


def _workspace_payload(workspace_id: uuid.UUID) -> WorkspaceModel:
    return WorkspaceModel.model_validate(
        {
            "id": str(workspace_id),
            "slug": "packet-workspace",
            "name": "Packet Workspace",
            "description": "Graph neighborhood tests",
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )


def _project_payload(project_id: uuid.UUID, workspace_id: uuid.UUID) -> ProjectModel:
    return ProjectModel.model_validate(
        {
            "id": str(project_id),
            "workspace_id": str(workspace_id),
            "slug": "packet-project",
            "name": "Packet Project",
            "description": "Graph neighborhood tests",
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )


def _task_payload(
    *,
    task_id: uuid.UUID,
    project_id: uuid.UUID,
    title: str,
    created_at: str,
) -> TaskModel:
    contract = _coding_contract(
        spec="## Goal\nCompile one packet.",
        file_scope=[
            "apps/api/src/agenticqueue_api/compiler.py",
            "tests/aq/test_graph_neighborhood.py",
        ],
        surface_area=["packet", "compiler", "graph"],
    )
    return TaskModel.model_validate(
        {
            "id": str(task_id),
            "project_id": str(project_id),
            "task_type": "coding-task",
            "title": title,
            "state": "queued",
            "description": contract["spec"],
            "contract": contract,
            "definition_of_done": contract["dod_checklist"],
            "created_at": created_at,
            "updated_at": created_at,
        }
    )


def _run_payload(
    *,
    run_id: uuid.UUID,
    task_id: uuid.UUID,
    actor_id: uuid.UUID,
    started_at: str,
) -> RunModel:
    return RunModel.model_validate(
        {
            "id": str(run_id),
            "task_id": str(task_id),
            "actor_id": str(actor_id),
            "status": "completed",
            "started_at": started_at,
            "ended_at": started_at,
            "summary": "Graph neighborhood run",
            "details": {},
            "created_at": started_at,
            "updated_at": started_at,
        }
    )


def _decision_payload(
    *,
    decision_id: uuid.UUID,
    task_id: uuid.UUID,
    run_id: uuid.UUID,
    actor_id: uuid.UUID,
    summary: str,
    decided_at: str,
) -> DecisionModel:
    return DecisionModel.model_validate(
        {
            "id": str(decision_id),
            "task_id": str(task_id),
            "run_id": str(run_id),
            "actor_id": str(actor_id),
            "summary": summary,
            "rationale": "AQ-71 graph neighborhood coverage.",
            "decided_at": decided_at,
            "embedding": None,
            "created_at": decided_at,
        }
    )


def _edge_payload(
    *,
    edge_id: uuid.UUID,
    src_id: uuid.UUID,
    dst_id: uuid.UUID,
    relation: EdgeRelation,
) -> EdgeModel:
    return EdgeModel.model_validate(
        {
            "id": str(edge_id),
            "src_entity_type": "decision",
            "src_id": str(src_id),
            "dst_entity_type": "decision",
            "dst_id": str(dst_id),
            "relation": relation.value,
            "metadata": {},
            "created_by": None,
            "created_at": "2026-04-20T00:00:00+00:00",
        }
    )


def _seed_graph_fixture(db_session: Session) -> uuid.UUID:
    actor_id = uuid.UUID("00000000-0000-0000-0000-000000000901")
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000902")
    project_id = uuid.UUID("00000000-0000-0000-0000-000000000903")
    task_ids = [
        uuid.UUID(f"00000000-0000-0000-0000-{suffix:012d}")
        for suffix in range(904, 910)
    ]
    run_ids = [
        uuid.UUID(f"00000000-0000-0000-0000-{suffix:012d}")
        for suffix in range(920, 926)
    ]
    decision_ids = [
        uuid.UUID(f"00000000-0000-0000-0000-{suffix:012d}")
        for suffix in range(930, 936)
    ]

    create_actor(db_session, _actor_payload(actor_id))
    create_workspace(db_session, _workspace_payload(workspace_id))
    create_project(db_session, _project_payload(project_id, workspace_id))

    decision_specs = [
        (
            task_ids[0],
            run_ids[0],
            decision_ids[0],
            "Decision newest",
            "2026-04-20T00:05:00+00:00",
        ),
        (
            task_ids[1],
            run_ids[1],
            decision_ids[1],
            "Decision fourth",
            "2026-04-20T00:04:00+00:00",
        ),
        (
            task_ids[2],
            run_ids[2],
            decision_ids[2],
            "Decision third",
            "2026-04-20T00:03:00+00:00",
        ),
        (
            task_ids[3],
            run_ids[3],
            decision_ids[3],
            "Decision second",
            "2026-04-20T00:02:00+00:00",
        ),
        (
            task_ids[4],
            run_ids[4],
            decision_ids[4],
            "Decision oldest",
            "2026-04-20T00:01:00+00:00",
        ),
        (
            task_ids[5],
            run_ids[5],
            decision_ids[5],
            "Ignored related-only decision",
            "2026-04-20T00:06:00+00:00",
        ),
    ]
    for task_id, run_id, decision_id, title, decided_at in decision_specs:
        create_task(
            db_session,
            _task_payload(
                task_id=task_id,
                project_id=project_id,
                title=title,
                created_at=decided_at,
            ),
        )
        create_run(
            db_session,
            _run_payload(
                run_id=run_id,
                task_id=task_id,
                actor_id=actor_id,
                started_at=decided_at,
            ),
        )
        create_decision(
            db_session,
            _decision_payload(
                decision_id=decision_id,
                task_id=task_id,
                run_id=run_id,
                actor_id=actor_id,
                summary=title,
                decided_at=decided_at,
            ),
        )

    create_edge(
        db_session,
        _edge_payload(
            edge_id=uuid.UUID("00000000-0000-0000-0000-000000000950"),
            src_id=decision_ids[0],
            dst_id=decision_ids[1],
            relation=EdgeRelation.INFORMED_BY,
        ),
    )
    create_edge(
        db_session,
        _edge_payload(
            edge_id=uuid.UUID("00000000-0000-0000-0000-000000000951"),
            src_id=decision_ids[0],
            dst_id=decision_ids[2],
            relation=EdgeRelation.IMPLEMENTS,
        ),
    )
    create_edge(
        db_session,
        _edge_payload(
            edge_id=uuid.UUID("00000000-0000-0000-0000-000000000952"),
            src_id=decision_ids[1],
            dst_id=decision_ids[3],
            relation=EdgeRelation.SUPERSEDES,
        ),
    )
    create_edge(
        db_session,
        _edge_payload(
            edge_id=uuid.UUID("00000000-0000-0000-0000-000000000953"),
            src_id=decision_ids[2],
            dst_id=decision_ids[4],
            relation=EdgeRelation.INFORMED_BY,
        ),
    )
    create_edge(
        db_session,
        _edge_payload(
            edge_id=uuid.UUID("00000000-0000-0000-0000-000000000954"),
            src_id=decision_ids[0],
            dst_id=decision_ids[5],
            relation=EdgeRelation.RELATED_TO,
        ),
    )

    return task_ids[0]


def test_packet_decisions_returns_exact_ancestor_chain_in_recency_order(
    db_session: Session,
) -> None:
    task_id = _seed_graph_fixture(db_session)

    decisions = packet_decisions(db_session, task_id, max_hops=3)

    assert [decision.summary for decision in decisions] == [
        "Decision newest",
        "Decision fourth",
        "Decision third",
        "Decision second",
        "Decision oldest",
    ]


def test_packet_decisions_respects_max_node_cap(
    db_session: Session,
) -> None:
    task_id = _seed_graph_fixture(db_session)

    decisions = packet_decisions(db_session, task_id, max_hops=3, max_nodes=3)

    assert [decision.summary for decision in decisions] == [
        "Decision newest",
        "Decision fourth",
        "Decision third",
    ]
