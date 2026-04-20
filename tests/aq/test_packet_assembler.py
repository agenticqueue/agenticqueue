from __future__ import annotations

import json
from pathlib import Path
import time
from types import SimpleNamespace
import uuid
from typing import Any, Iterator, cast

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
) -> TaskModel:
    contract = _coding_contract(
        spec=spec,
        file_scope=[
            "apps/api/src/agenticqueue_api/compiler.py",
            "tests/aq/test_packet_assembler.py",
        ],
        surface_area=["packet", "compiler", "graph"],
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
) -> LearningModel:
    return LearningModel.model_validate(
        {
            "id": str(learning_id),
            "task_id": str(task_id),
            "owner_actor_id": None,
            "owner": "packet-compiler",
            "title": title,
            "learning_type": "pattern",
            "what_happened": "The packet compiler benefited from deterministic graph context.",
            "what_learned": "Graph-first packet assembly keeps the hot path fast.",
            "action_rule": "Use graph traversal before fuzzier learning retrieval.",
            "applies_when": "A coding-task packet needs a fast, deterministic packet.",
            "does_not_apply_when": "The packet is doing a deliberate fuzzy global search.",
            "evidence": ["tests/aq/test_packet_assembler.py"],
            "scope": "project",
            "confidence": "confirmed",
            "status": "active",
            "promotion_eligible": False,
            "review_date": "2026-05-01",
            "embedding": None,
            "created_at": "2026-04-20T00:03:00+00:00",
            "updated_at": "2026-04-20T00:03:00+00:00",
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
    assert first_packet.retrieval_tiers_used == ["graph", "surface"]
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
    create_learning(
        db_session,
        _learning_payload(
            learning_id=uuid.UUID("00000000-0000-0000-0000-000000000806"),
            task_id=source_task_id,
            title="One reusable learning",
        ),
    )

    packet = compiler_module.compile_packet(
        db_session,
        task_id,
        learning_limit=2,
        task_type_registry=task_type_registry,
        policy_registry=policy_registry,
    )

    assert packet["retrieval_tiers_used"] == ["graph", "surface", "vector"]
    assert packet["open_questions"] == []
    assert len(packet["relevant_learnings"]) == 1


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
        compiler_module, "rank_learnings_for_task", lambda *args, **kwargs: []
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
    assert (
        compiler_module._learning_similarity_text(
            cast(
                Any,
                SimpleNamespace(
                    title="A",
                    action_rule="B",
                    what_happened="C",
                    what_learned="D",
                    evidence=["E"],
                ),
            )
        )
        == "A\nB\nC\nD\nE"
    )

    vector_learnings = compiler_module._vector_fallback_learnings(
        db_session,
        target_record,
        exclude_ids=set(),
        limit=1,
    )
    assert [learning.title for learning in vector_learnings] == [
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
            compiler_module, "cosine_similarity", lambda *args, **kwargs: 0.0
        )
        assert (
            compiler_module._vector_fallback_learnings(
                db_session,
                target_record,
                exclude_ids=set(),
                limit=1,
            )
            == []
        )
    finally:
        monkeypatch.undo()


def test_assemble_packet_raises_for_missing_task(db_session: Session) -> None:
    with pytest.raises(KeyError):
        assemble_packet(
            db_session,
            uuid.UUID("00000000-0000-0000-0000-000000000999"),
        )
