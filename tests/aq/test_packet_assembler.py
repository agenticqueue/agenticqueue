from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import time
import uuid
from typing import Any, Iterator

import pytest
import sqlalchemy as sa
import yaml  # type: ignore[import-untyped]
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import agenticqueue_api.compiler as compiler_module
from agenticqueue_api.compiler import assemble_packet
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import (
    ActorModel,
    ArtifactModel,
    DecisionModel,
    EdgeModel,
    EdgeRelation,
    LearningModel,
    ProjectModel,
    RunModel,
    TaskModel,
    WorkspaceModel,
)
from agenticqueue_api.policy.loader import PolicyRegistry
from agenticqueue_api.retrieval import (
    RetrievalQuery,
    RetrievalResult,
    RetrievalScope,
    RetrievalService,
)
from agenticqueue_api.retrieval.config import RetrievalConfig, load_retrieval_config
from agenticqueue_api.retrieval.tiers import vector as vector_tier
from agenticqueue_api.repo_scope import resolve_repo_scope
from agenticqueue_api.repo import (
    create_actor,
    create_artifact,
    create_decision,
    create_edge,
    create_learning,
    create_project,
    create_run,
    create_task,
    create_workspace,
)
from agenticqueue_api.task_type_registry import TaskTypeRegistry

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


def _coding_schema() -> dict[str, Any]:
    path = _repo_root() / "task_types" / "coding-task.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


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


def _truncate_all_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        qualified_tables = ", ".join(
            f"agenticqueue.{table_name}" for table_name in TRUNCATE_TABLES
        )
        connection.execute(
            sa.text(f"TRUNCATE TABLE {qualified_tables} RESTART IDENTITY CASCADE")
        )


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    _truncate_all_tables(engine)
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
def temp_registries(tmp_path: Path) -> tuple[TaskTypeRegistry, PolicyRegistry]:
    task_types_dir = tmp_path / "task_types"
    task_types_dir.mkdir(parents=True)
    policies_dir = tmp_path / "policies"
    policies_dir.mkdir(parents=True)

    (task_types_dir / "coding-task.schema.json").write_text(
        json.dumps(_coding_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (task_types_dir / "coding-task.policy.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "1.0.0",
                "hitl_required": True,
                "autonomy_tier": 3,
                "capabilities": [
                    "read_repo",
                    "write_branch",
                    "run_tests",
                    "update_task",
                ],
                "body": {
                    "transitions": {"queued": ["claimed"]},
                    "enable_fuzzy_global_search": True,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (policies_dir / "default-coding.policy.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "1.0.0",
                "hitl_required": False,
                "autonomy_tier": 2,
                "capabilities": ["read_repo"],
                "body": {"enable_fuzzy_global_search": True},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    task_type_registry = TaskTypeRegistry(task_types_dir)
    task_type_registry.load()
    policy_registry = PolicyRegistry(policies_dir)
    policy_registry.load()
    return task_type_registry, policy_registry


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
            "description": "Packet assembler tests",
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
            "description": "Packet assembler tests",
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )


def _task_payload(
    *,
    task_id: uuid.UUID,
    project_id: uuid.UUID,
    title: str,
    spec: str,
    created_at: str,
    state: str = "queued",
    file_scope: list[str] | None = None,
    surface_area: list[str] | None = None,
) -> TaskModel:
    contract = _coding_contract(
        spec=spec,
        file_scope=file_scope
        or [
            "apps/api/src/agenticqueue_api/compiler.py",
            "tests/aq/test_packet_assembler.py",
        ],
        surface_area=surface_area or ["packet", "compiler", "graph"],
    )
    return TaskModel.model_validate(
        {
            "id": str(task_id),
            "project_id": str(project_id),
            "task_type": "coding-task",
            "title": title,
            "state": state,
            "description": spec,
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
            "summary": "Packet compiler run",
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
            "rationale": "Graph ancestors should surface this decision.",
            "decided_at": decided_at,
            "embedding": None,
            "created_at": decided_at,
        }
    )


def _artifact_payload(
    *,
    artifact_id: uuid.UUID,
    task_id: uuid.UUID,
    run_id: uuid.UUID,
) -> ArtifactModel:
    return ArtifactModel.model_validate(
        {
            "id": str(artifact_id),
            "task_id": str(task_id),
            "run_id": str(run_id),
            "kind": "patch",
            "uri": "artifacts/diffs/aq-70.patch",
            "details": {"format": "unified-diff"},
            "embedding": None,
            "created_at": "2026-04-20T00:05:00+00:00",
            "updated_at": "2026-04-20T00:05:00+00:00",
        }
    )


def _learning_payload(
    *,
    learning_id: uuid.UUID,
    task_id: uuid.UUID,
    title: str,
    owner: str = "packet-compiler",
    learning_type: str = "pattern",
    scope: str = "project",
    created_at: str = "2026-04-20T00:03:00+00:00",
) -> LearningModel:
    return LearningModel.model_validate(
        {
            "id": str(learning_id),
            "task_id": str(task_id),
            "owner_actor_id": None,
            "owner": owner,
            "title": title,
            "learning_type": learning_type,
            "what_happened": "The packet compiler benefited from deterministic graph context.",
            "what_learned": "Graph-first packet assembly keeps the hot path fast.",
            "action_rule": "Use graph traversal before fuzzier learning retrieval.",
            "applies_when": "A coding-task packet needs a fast, deterministic packet.",
            "does_not_apply_when": "The packet is doing a deliberate fuzzy global search.",
            "evidence": ["tests/aq/test_packet_assembler.py"],
            "scope": scope,
            "confidence": "confirmed",
            "status": "active",
            "promotion_eligible": False,
            "review_date": "2026-05-01",
            "embedding": None,
            "created_at": created_at,
            "updated_at": created_at,
        }
    )


def _edge_payload(
    *,
    edge_id: uuid.UUID,
    src_entity_type: str,
    src_id: uuid.UUID,
    dst_entity_type: str,
    dst_id: uuid.UUID,
    relation: EdgeRelation,
) -> EdgeModel:
    return EdgeModel.model_validate(
        {
            "id": str(edge_id),
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


def _seed_graph_fixture(db_session: Session) -> uuid.UUID:
    actor_id = uuid.UUID("00000000-0000-0000-0000-000000000701")
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000702")
    project_id = uuid.UUID("00000000-0000-0000-0000-000000000703")
    dependency_task_id = uuid.UUID("00000000-0000-0000-0000-000000000704")
    dependency_run_id = uuid.UUID("00000000-0000-0000-0000-000000000705")
    decision_id = uuid.UUID("00000000-0000-0000-0000-000000000706")
    target_task_id = uuid.UUID("00000000-0000-0000-0000-000000000707")
    target_run_id = uuid.UUID("00000000-0000-0000-0000-000000000708")
    artifact_id = uuid.UUID("00000000-0000-0000-0000-000000000709")
    learning_id = uuid.UUID("00000000-0000-0000-0000-000000000710")

    create_actor(db_session, _actor_payload(actor_id))
    create_workspace(db_session, _workspace_payload(workspace_id))
    create_project(db_session, _project_payload(project_id, workspace_id))
    create_task(
        db_session,
        _task_payload(
            task_id=dependency_task_id,
            project_id=project_id,
            title="Dependency task",
            spec="Dependency task for packet traversal.",
            created_at="2026-04-20T00:00:00+00:00",
            state="done",
        ),
    )
    create_run(
        db_session,
        _run_payload(
            run_id=dependency_run_id,
            task_id=dependency_task_id,
            actor_id=actor_id,
            started_at="2026-04-20T00:01:00+00:00",
        ),
    )
    create_decision(
        db_session,
        _decision_payload(
            decision_id=decision_id,
            task_id=dependency_task_id,
            run_id=dependency_run_id,
            actor_id=actor_id,
            summary="Dependency decision",
            decided_at="2026-04-20T00:02:00+00:00",
        ),
    )
    create_task(
        db_session,
        _task_payload(
            task_id=target_task_id,
            project_id=project_id,
            title="Assemble the packet",
            spec=(
                "## Goal\nCompile one packet.\n\n"
                "## Open Questions\n"
                "- Who owns packet TTL?\n"
                "- Should packet caching flush on new learnings?\n"
            ),
            created_at="2026-04-20T00:04:00+00:00",
        ),
    )
    create_run(
        db_session,
        _run_payload(
            run_id=target_run_id,
            task_id=target_task_id,
            actor_id=actor_id,
            started_at="2026-04-20T00:05:00+00:00",
        ),
    )
    create_artifact(
        db_session,
        _artifact_payload(
            artifact_id=artifact_id,
            task_id=target_task_id,
            run_id=target_run_id,
        ),
    )
    create_learning(
        db_session,
        _learning_payload(
            learning_id=learning_id,
            task_id=dependency_task_id,
            title="Prefer graph-first packet assembly",
        ),
    )
    create_edge(
        db_session,
        _edge_payload(
            edge_id=uuid.UUID("00000000-0000-0000-0000-000000000711"),
            src_entity_type="task",
            src_id=dependency_task_id,
            dst_entity_type="task",
            dst_id=target_task_id,
            relation=EdgeRelation.DEPENDS_ON,
        ),
    )
    create_edge(
        db_session,
        _edge_payload(
            edge_id=uuid.UUID("00000000-0000-0000-0000-000000000712"),
            src_entity_type="decision",
            src_id=decision_id,
            dst_entity_type="task",
            dst_id=dependency_task_id,
            relation=EdgeRelation.TRIGGERED,
        ),
    )

    return target_task_id


def test_assemble_packet_returns_stable_golden_payload(
    db_session: Session,
) -> None:
    target_task_id = _seed_graph_fixture(db_session)
    expected_scope = resolve_repo_scope(
        _repo_root(),
        [
            "apps/api/src/agenticqueue_api/compiler.py",
            "tests/aq/test_packet_assembler.py",
        ],
        max_files=200,
    )

    first_packet = assemble_packet(db_session, target_task_id)
    second_packet = assemble_packet(db_session, target_task_id)

    assert first_packet.packet_version_id == second_packet.packet_version_id
    assert first_packet.model_dump(mode="json") == second_packet.model_dump(mode="json")
    assert first_packet.retrieval_tiers_used == ["surface_area", "graph", "metadata"]
    assert first_packet.open_questions == [
        "Who owns packet TTL?",
        "Should packet caching flush on new learnings?",
    ]
    assert [decision.summary for decision in first_packet.relevant_decisions] == [
        "Dependency decision"
    ]
    assert [learning.title for learning in first_packet.relevant_learnings] == [
        "Prefer graph-first packet assembly"
    ]
    assert [artifact.uri for artifact in first_packet.linked_artifacts] == [
        "artifacts/diffs/aq-70.patch"
    ]
    assert first_packet.permissions.policy_name == "coding-task"
    assert first_packet.permissions.hitl_required is True
    assert first_packet.permissions.capabilities == [
        "read_repo",
        "write_branch",
        "run_tests",
        "update_task",
    ]
    assert first_packet.repo_scope.repo == "github.com/agenticqueue/agenticqueue"
    assert first_packet.repo_scope.branch == "main"
    assert first_packet.repo_scope.file_scope == expected_scope.file_scope
    assert first_packet.repo_scope.surface_area == ["packet", "compiler", "graph"]
    assert (
        first_packet.repo_scope.estimated_token_count
        == expected_scope.estimated_token_count
    )
    assert first_packet.expected_output_schema["required"] == [
        "diff_url",
        "test_report",
        "artifacts",
        "learnings",
    ]


def test_assemble_packet_marks_vector_tier_when_fuzzy_search_is_enabled(
    db_session: Session,
    temp_registries: tuple[TaskTypeRegistry, PolicyRegistry],
) -> None:
    task_type_registry, policy_registry = temp_registries

    actor_id = uuid.UUID("00000000-0000-0000-0000-000000000801")
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000802")
    project_id = uuid.UUID("00000000-0000-0000-0000-000000000803")
    task_id = uuid.UUID("00000000-0000-0000-0000-000000000804")
    source_task_id = uuid.UUID("00000000-0000-0000-0000-000000000805")
    vector_source_task_id = uuid.UUID("00000000-0000-0000-0000-000000000807")

    create_actor(db_session, _actor_payload(actor_id))
    create_workspace(db_session, _workspace_payload(workspace_id))
    create_project(db_session, _project_payload(project_id, workspace_id))
    create_task(
        db_session,
        _task_payload(
            task_id=source_task_id,
            project_id=project_id,
            title="Prior learning source",
            spec="One prior learning exists.",
            created_at="2026-04-20T00:00:00+00:00",
            state="done",
        ),
    )
    create_task(
        db_session,
        _task_payload(
            task_id=task_id,
            project_id=project_id,
            title="Vector fallback task",
            spec="No open questions here.",
            created_at="2026-04-20T00:01:00+00:00",
        ),
    )
    create_task(
        db_session,
        _task_payload(
            task_id=vector_source_task_id,
            project_id=project_id,
            title="Vector fallback source",
            spec="Vector fallback task.",
            created_at="2026-04-20T00:02:00+00:00",
            state="done",
            surface_area=["vector", "fallback"],
        ),
    )
    create_learning(
        db_session,
        _learning_payload(
            learning_id=uuid.UUID("00000000-0000-0000-0000-000000000806"),
            task_id=source_task_id,
            title="One reusable learning",
        ),
    )
    create_learning(
        db_session,
        _learning_payload(
            learning_id=uuid.UUID("00000000-0000-0000-0000-000000000808"),
            task_id=vector_source_task_id,
            title="Vector fallback learning",
        ),
    )

    packet = compiler_module.compile_packet(
        db_session,
        task_id,
        learning_limit=2,
        task_type_registry=task_type_registry,
        policy_registry=policy_registry,
    )

    assert packet["retrieval_tiers_used"][:3] == [
        "surface_area",
        "graph",
        "metadata",
    ]
    assert "rerank" in packet["retrieval_tiers_used"]
    assert any(
        tier in packet["retrieval_tiers_used"] for tier in ("fts", "trgm", "vector")
    )
    assert packet["open_questions"] == []
    assert len(packet["relevant_learnings"]) == 2
    assert {learning["title"] for learning in packet["relevant_learnings"]} == {
        "One reusable learning",
        "Vector fallback learning",
    }


def test_assemble_packet_graph_only_hot_path_stays_under_ten_ms(
    db_session: Session,
    temp_registries: tuple[TaskTypeRegistry, PolicyRegistry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_type_registry, policy_registry = temp_registries
    actor_id = uuid.UUID("00000000-0000-0000-0000-000000000821")
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000822")
    project_id = uuid.UUID("00000000-0000-0000-0000-000000000823")
    task_id = uuid.UUID("00000000-0000-0000-0000-000000000824")

    create_actor(db_session, _actor_payload(actor_id))
    create_workspace(db_session, _workspace_payload(workspace_id))
    create_project(db_session, _project_payload(project_id, workspace_id))
    create_task(
        db_session,
        _task_payload(
            task_id=task_id,
            project_id=project_id,
            title="Hot path task",
            spec="## Goal\nCompile a fast packet.",
            created_at="2026-04-20T00:00:00+00:00",
        ),
    )

    monkeypatch.setattr(
        RetrievalService,
        "retrieve",
        lambda self, query: RetrievalResult(
            items=[],
            tiers_fired=["surface_area", "graph", "metadata"],
        ),
    )

    assemble_packet(
        db_session,
        task_id,
        task_type_registry=task_type_registry,
        policy_registry=policy_registry,
    )
    warm_start = time.perf_counter()
    assemble_packet(
        db_session,
        task_id,
        task_type_registry=task_type_registry,
        policy_registry=policy_registry,
    )
    elapsed_ms = (time.perf_counter() - warm_start) * 1000

    assert elapsed_ms < 10


def test_packet_compiler_helper_branches_and_vector_candidates(
    db_session: Session,
    temp_registries: tuple[TaskTypeRegistry, PolicyRegistry],
) -> None:
    task_type_registry, policy_registry = temp_registries
    actor_id = uuid.UUID("00000000-0000-0000-0000-000000000841")
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000842")
    project_id = uuid.UUID("00000000-0000-0000-0000-000000000843")
    source_task_id = uuid.UUID("00000000-0000-0000-0000-000000000844")
    target_task_id = uuid.UUID("00000000-0000-0000-0000-000000000845")

    create_actor(db_session, _actor_payload(actor_id))
    create_workspace(db_session, _workspace_payload(workspace_id))
    create_project(db_session, _project_payload(project_id, workspace_id))
    create_task(
        db_session,
        _task_payload(
            task_id=source_task_id,
            project_id=project_id,
            title="Learning source",
            spec="Source task.",
            created_at="2026-04-20T00:00:00+00:00",
            state="done",
        ),
    )
    create_task(
        db_session,
        TaskModel.model_validate(
            {
                "id": str(target_task_id),
                "project_id": str(project_id),
                "task_type": "coding-task",
                "title": "Branch coverage task",
                "state": "queued",
                "description": "## Open Questions\nnotes only\n- keep bullet\n### Next\n",
                "contract": {
                    "repo": None,
                    "branch": 42,
                    "file_scope": [
                        "  apps/api/src/agenticqueue_api/compiler.py  ",
                        "",
                        3,
                    ],
                    "surface_area": "packet",
                    "spec": "## Open Questions\nnotes only\n- keep bullet\n### Next\n",
                    "dod_checklist": ["done"],
                    "autonomy_tier": 3,
                    "output": {},
                },
                "definition_of_done": ["done"],
                "created_at": "2026-04-20T00:01:00+00:00",
                "updated_at": "2026-04-20T00:01:00+00:00",
            }
        ),
    )
    create_learning(
        db_session,
        _learning_payload(
            learning_id=uuid.UUID("00000000-0000-0000-0000-000000000846"),
            task_id=source_task_id,
            title="Branch coverage task compiler learning",
        ),
    )

    target_record = db_session.get(compiler_module.TaskRecord, target_task_id)
    assert target_record is not None

    assert compiler_module._normalize_string_list("not-a-list") == []
    assert compiler_module._normalize_string_list([" keep ", "", 3]) == ["keep"]
    assert compiler_module._normalize_string(7) == ""
    assert compiler_module._extract_open_questions(target_record) == ["keep bullet"]

    definition_no_properties = compiler_module.TaskTypeDefinition(
        name="coding-task",
        schema={"type": "object"},
        policy={"version": "1.0.0", "hitl_required": True, "autonomy_tier": 3},
        schema_path=Path("coding-task.schema.json"),
        policy_path=Path("coding-task.policy.yaml"),
    )
    definition_bad_output = compiler_module.TaskTypeDefinition(
        name="coding-task",
        schema={"type": "object", "properties": {"output": "bad"}},
        policy={"version": "1.0.0", "hitl_required": True, "autonomy_tier": 3},
        schema_path=Path("coding-task.schema.json"),
        policy_path=Path("coding-task.policy.yaml"),
    )
    assert compiler_module._expected_output_schema(definition_no_properties) == {}
    assert compiler_module._expected_output_schema(definition_bad_output) == {}
    retrieval_service = RetrievalService(db_session)
    candidates = retrieval_service._candidate_pool(project_id=project_id)
    source_candidate = next(
        candidate
        for candidate in candidates
        if candidate.learning.title == "Branch coverage task compiler learning"
    )
    assert "Branch coverage task" in vector_tier.task_similarity_text(target_record)
    learning_similarity_text = vector_tier.learning_similarity_text(source_candidate)
    assert "Branch coverage task compiler learning" in learning_similarity_text
    assert (
        "Use graph traversal before fuzzier learning retrieval."
        in learning_similarity_text
    )
    assert "tests/aq/test_packet_assembler.py" in learning_similarity_text
    assert "Source task." in learning_similarity_text

    vector_learnings = vector_tier.vector_candidates(
        db_session,
        task=target_record,
        candidates=candidates,
        exclude_ids=set(),
        limit=1,
    )
    assert [candidate.learning.title for candidate in vector_learnings] == [
        "Branch coverage task compiler learning"
    ]

    packet = compiler_module.compile_packet(
        db_session,
        target_task_id,
        task_type_registry=task_type_registry,
        policy_registry=policy_registry,
    )
    assert packet["repo_scope"]["repo"] == ""
    assert packet["repo_scope"]["branch"] == ""
    assert packet["repo_scope"]["file_scope"] == [
        "apps/api/src/agenticqueue_api/compiler.py"
    ]
    assert packet["repo_scope"]["surface_area"] == []
    assert packet["repo_scope"]["estimated_token_count"] > 0
    assert packet["open_questions"] == ["keep bullet"]

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(
            vector_tier, "cosine_similarity", lambda *args, **kwargs: 0.0
        )
        assert (
            vector_tier.vector_candidates(
                db_session,
                task=target_record,
                candidates=candidates,
                exclude_ids=set(),
                limit=1,
            )
            == []
        )
    finally:
        monkeypatch.undo()


def test_retrieval_service_applies_metadata_filters_and_branch_helpers(
    db_session: Session,
) -> None:
    actor_id = uuid.UUID("00000000-0000-0000-0000-000000000901")
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000902")
    project_id = uuid.UUID("00000000-0000-0000-0000-000000000903")
    target_task_id = uuid.UUID("00000000-0000-0000-0000-000000000904")
    source_task_id = uuid.UUID("00000000-0000-0000-0000-000000000905")
    stale_task_id = uuid.UUID("00000000-0000-0000-0000-000000000906")

    create_actor(db_session, _actor_payload(actor_id))
    create_workspace(db_session, _workspace_payload(workspace_id))
    create_project(db_session, _project_payload(project_id, workspace_id))
    create_task(
        db_session,
        _task_payload(
            task_id=source_task_id,
            project_id=project_id,
            title="Metadata source",
            spec="Metadata source task.",
            created_at="2026-04-20T00:00:00+00:00",
            state="done",
            surface_area=["retrieval", "filter"],
        ),
    )
    create_task(
        db_session,
        _task_payload(
            task_id=stale_task_id,
            project_id=project_id,
            title="Stale metadata source",
            spec="Stale metadata source task.",
            created_at="2026-03-01T00:00:00+00:00",
            state="done",
            surface_area=["retrieval", "filter"],
        ),
    )
    create_task(
        db_session,
        _task_payload(
            task_id=target_task_id,
            project_id=project_id,
            title="Metadata retrieval target",
            spec="Filter retrieval results.",
            created_at="2026-04-20T00:05:00+00:00",
            surface_area=["retrieval", "filter"],
        ),
    )
    create_learning(
        db_session,
        _learning_payload(
            learning_id=uuid.UUID("00000000-0000-0000-0000-000000000907"),
            task_id=source_task_id,
            title="Metadata match",
            owner="owner-a",
            learning_type="pattern",
            scope="project",
            created_at="2026-04-20T00:01:00+00:00",
        ),
    )
    create_learning(
        db_session,
        _learning_payload(
            learning_id=uuid.UUID("00000000-0000-0000-0000-000000000908"),
            task_id=source_task_id,
            title="Wrong owner",
            owner="owner-b",
            learning_type="pattern",
            scope="project",
            created_at="2026-04-20T00:02:00+00:00",
        ),
    )
    create_learning(
        db_session,
        _learning_payload(
            learning_id=uuid.UUID("00000000-0000-0000-0000-000000000909"),
            task_id=stale_task_id,
            title="Too old",
            owner="owner-a",
            learning_type="pattern",
            scope="project",
            created_at="2026-03-01T00:00:00+00:00",
        ),
    )

    service = RetrievalService(
        db_session,
        config=RetrievalConfig(vector_project_scope_only=False),
    )
    result = service.retrieve(
        RetrievalQuery(
            task_id=target_task_id,
            layers=("project",),
            scope=RetrievalScope(
                owners=("owner-a",),
                learning_types=("pattern",),
                max_age_days=14,
            ),
        )
    )

    assert result.tiers_fired == ["surface_area", "graph", "metadata"]
    assert [learning.title for learning in result.items] == ["Metadata match"]

    candidates = service._candidate_pool(project_id=project_id)
    assert len(service._vector_pool(candidates, project_id=project_id)) == len(
        candidates
    )
    merged = service._merge_candidates(
        [candidates[0]],
        [replace(candidates[0], vector_similarity=0.9)],
    )
    assert merged[0].vector_similarity == 0.9
    merged = service._merge_candidates(
        [replace(candidates[0], vector_similarity=0.9)],
        [replace(candidates[0], vector_similarity=0.1)],
    )
    assert merged[0].vector_similarity == 0.9


def test_retrieval_service_handles_missing_tasks_and_empty_projects(
    db_session: Session,
) -> None:
    service = RetrievalService(db_session)

    with pytest.raises(KeyError):
        service.retrieve(
            RetrievalQuery(
                task_id=uuid.UUID("00000000-0000-0000-0000-000000000950"),
            )
        )

    actor_id = uuid.UUID("00000000-0000-0000-0000-000000000951")
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000952")
    project_id = uuid.UUID("00000000-0000-0000-0000-000000000953")
    task_id = uuid.UUID("00000000-0000-0000-0000-000000000954")

    create_actor(db_session, _actor_payload(actor_id))
    create_workspace(db_session, _workspace_payload(workspace_id))
    create_project(db_session, _project_payload(project_id, workspace_id))
    create_task(
        db_session,
        _task_payload(
            task_id=task_id,
            project_id=project_id,
            title="No learnings yet",
            spec="A fresh task with no persisted learnings.",
            created_at="2026-04-20T00:00:00+00:00",
            surface_area=["empty", "retrieval"],
        ),
    )

    result = service.retrieve(RetrievalQuery(task_id=task_id))
    assert result.items == []
    assert result.tiers_fired == ["surface_area", "graph", "metadata"]

    fuzzy_result = service.retrieve(
        RetrievalQuery(task_id=task_id, fuzzy_global_search=True)
    )
    assert fuzzy_result.items == []
    assert fuzzy_result.tiers_fired == ["surface_area", "graph", "metadata"]


def test_retrieval_service_candidate_pool_handles_detached_learnings(
    db_session: Session,
) -> None:
    detached_payload = _learning_payload(
        learning_id=uuid.UUID("00000000-0000-0000-0000-000000000955"),
        task_id=uuid.UUID("00000000-0000-0000-0000-000000000956"),
        title="Detached learning",
    ).model_dump(mode="json")
    detached_payload["task_id"] = None
    create_learning(db_session, LearningModel.model_validate(detached_payload))

    service = RetrievalService(db_session)
    candidates = service._candidate_pool(project_id=None)
    assert len(candidates) == 1
    assert candidates[0].learning.title == "Detached learning"
    assert candidates[0].source_task is None

    scoped_candidates = service._vector_pool(
        candidates, project_id=uuid.UUID("00000000-0000-0000-0000-000000000957")
    )
    assert scoped_candidates == []


def test_retrieval_service_fixture_metrics_and_cold_path(
    db_session: Session,
) -> None:
    actor_id = uuid.UUID("00000000-0000-0000-0000-000000000961")
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000962")
    project_id = uuid.UUID("00000000-0000-0000-0000-000000000963")
    domains = ["packet", "retrieval", "policy", "cache", "vector"]

    create_actor(db_session, _actor_payload(actor_id))
    create_workspace(db_session, _workspace_payload(workspace_id))
    create_project(db_session, _project_payload(project_id, workspace_id))

    expected_titles: dict[str, set[str]] = {}
    minute = 0
    for domain in domains:
        titles: set[str] = set()
        for index in range(10):
            task_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"https://agenticqueue.ai/tests/retrieval-source/{domain}/{index}",
            )
            learning_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"https://agenticqueue.ai/tests/retrieval-learning/{domain}/{index}",
            )
            created_at = f"2026-04-20T00:{minute:02d}:00+00:00"
            minute += 1
            create_task(
                db_session,
                _task_payload(
                    task_id=task_id,
                    project_id=project_id,
                    title=f"{domain.title()} source {index}",
                    spec=f"{domain} retrieval source task {index}.",
                    created_at=created_at,
                    state="done",
                    surface_area=[domain, f"{domain}-core"],
                ),
            )
            title = f"{domain.title()} learning {index}"
            create_learning(
                db_session,
                _learning_payload(
                    learning_id=learning_id,
                    task_id=task_id,
                    title=title,
                    owner=f"{domain}-owner",
                    learning_type="tooling" if index % 2 else "pattern",
                    scope=("task", "project", "global")[index % 3],
                    created_at=created_at,
                ),
            )
            titles.add(title)
        expected_titles[domain] = titles

    service = RetrievalService(db_session)
    total_hits = 0
    total_expected = 0
    hot_path_queries = 0

    for query_index in range(18):
        domain = domains[query_index % len(domains)]
        task_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"https://agenticqueue.ai/tests/retrieval-query/{domain}/{query_index}",
        )
        created_at = f"2026-04-20T01:{query_index:02d}:00+00:00"
        create_task(
            db_session,
            _task_payload(
                task_id=task_id,
                project_id=project_id,
                title=f"{domain.title()} hot query {query_index}",
                spec=f"{domain} retrieval hot path query {query_index}.",
                created_at=created_at,
                surface_area=[domain, f"{domain}-core"],
            ),
        )
        result = service.retrieve(
            RetrievalQuery(
                task_id=task_id,
                k=10,
                layers=("task", "project", "global"),
            )
        )
        actual_titles = {learning.title for learning in result.items}
        total_hits += len(actual_titles & expected_titles[domain])
        total_expected += 10
        if not any(tier in result.tiers_fired for tier in ("fts", "trgm", "vector")):
            hot_path_queries += 1
        assert result.tiers_fired == ["surface_area", "graph", "metadata"]

    for query_index in range(2):
        task_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"https://agenticqueue.ai/tests/retrieval-cold-query/{query_index}",
        )
        created_at = f"2026-04-20T02:{query_index:02d}:00+00:00"
        create_task(
            db_session,
            _task_payload(
                task_id=task_id,
                project_id=project_id,
                title=f"Vector fallback query {query_index}",
                spec="Vector retrieval fallback for recall-heavy query.",
                created_at=created_at,
                surface_area=[f"no-surface-match-{query_index}"],
            ),
        )
        result = service.retrieve(
            RetrievalQuery(
                task_id=task_id,
                k=10,
                fuzzy_global_search=True,
            )
        )
        actual_titles = {learning.title for learning in result.items}
        total_hits += len(actual_titles & expected_titles["vector"])
        total_expected += 10
        assert result.tiers_fired[:3] == ["surface_area", "graph", "metadata"]
        assert "rerank" in result.tiers_fired
        assert any(tier in result.tiers_fired for tier in ("fts", "trgm", "vector"))

    assert total_hits / total_expected >= 0.8
    assert hot_path_queries / 20 >= 0.9


def test_load_retrieval_config_uses_yaml_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "retrieval.yaml"
    config_path.write_text(
        "\n".join(
            [
                "vector_candidate_limit: 7",
                "vector_project_scope_only: false",
                "rerank:",
                "  lexical_weight: 0.4",
                "  recency_weight: 0.5",
                "  access_count_weight: 0.1",
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = load_retrieval_config(config_path)
    assert config.vector_candidate_limit == 7
    assert config.vector_project_scope_only is False
    assert config.rerank.lexical_weight == 0.4
    assert config.rerank.recency_weight == 0.5
    assert config.rerank.access_count_weight == 0.1

    config_path.write_text("vector_candidate_limit: 0\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_retrieval_config(config_path)


def test_assemble_packet_raises_for_missing_task(db_session: Session) -> None:
    with pytest.raises(KeyError):
        assemble_packet(
            db_session,
            uuid.UUID("00000000-0000-0000-0000-000000000999"),
        )
