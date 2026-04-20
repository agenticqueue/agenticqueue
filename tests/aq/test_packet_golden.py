from __future__ import annotations

# ruff: noqa: E402

import difflib
import json
from collections.abc import Callable
from pathlib import Path
import uuid

import pytest
from sqlalchemy.orm import Session

pytest_plugins = ["tests.aq.test_packet_assembler"]

from agenticqueue_api.compiler import compile_packet
from agenticqueue_api.models import EdgeRelation
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
from tests.aq.test_packet_assembler import (
    _actor_payload,
    _artifact_payload,
    _decision_payload,
    _edge_payload,
    _learning_payload,
    _project_payload,
    _run_payload,
    _task_payload,
    _workspace_payload,
)

EXPECTED_DIR = Path(__file__).resolve().parents[1] / "packets" / "expected"


def _fixture_uuid(suffix: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-0000-0000-{suffix:012x}")


def _task_for_case(
    *,
    task_id: uuid.UUID,
    project_id: uuid.UUID,
    title: str,
    spec: str,
    created_at: str,
    file_scope: list[str],
    surface_area: list[str],
    state: str = "queued",
):
    task = _task_payload(
        task_id=task_id,
        project_id=project_id,
        title=title,
        spec=spec,
        created_at=created_at,
        state=state,
    )
    payload = task.model_dump(mode="json")
    contract = dict(payload["contract"])
    contract["file_scope"] = file_scope
    contract["surface_area"] = surface_area
    payload["contract"] = contract
    payload["definition_of_done"] = contract["dod_checklist"]
    return type(task).model_validate(payload)


def _decision_for_case(
    *,
    decision_id: uuid.UUID,
    task_id: uuid.UUID,
    run_id: uuid.UUID,
    actor_id: uuid.UUID,
    summary: str,
    decided_at: str,
):
    decision = _decision_payload(
        decision_id=decision_id,
        task_id=task_id,
        run_id=run_id,
        actor_id=actor_id,
        summary=summary,
        decided_at=decided_at,
    )
    payload = decision.model_dump(mode="json")
    payload["summary"] = summary
    payload["decided_at"] = decided_at
    return type(decision).model_validate(payload)


def _artifact_for_case(
    *,
    artifact_id: uuid.UUID,
    task_id: uuid.UUID,
    run_id: uuid.UUID,
    uri: str,
    created_at: str,
):
    artifact = _artifact_payload(
        artifact_id=artifact_id,
        task_id=task_id,
        run_id=run_id,
    )
    payload = artifact.model_dump(mode="json")
    payload["uri"] = uri
    payload["created_at"] = created_at
    payload["updated_at"] = created_at
    return type(artifact).model_validate(payload)


def _learning_for_case(
    *,
    learning_id: uuid.UUID,
    task_id: uuid.UUID,
    title: str,
    evidence: str,
    created_at: str,
):
    learning = _learning_payload(
        learning_id=learning_id,
        task_id=task_id,
        title=title,
    )
    payload = learning.model_dump(mode="json")
    payload["title"] = title
    payload["evidence"] = [evidence]
    payload["created_at"] = created_at
    payload["updated_at"] = created_at
    return type(learning).model_validate(payload)


def _seed_case(
    db_session: Session,
    *,
    root: int,
    target_title: str,
    target_spec: str,
    dependency_title: str,
    dependency_spec: str,
    decision_summary: str,
    learning_title: str,
    learning_evidence: str,
    artifact_uri: str,
    file_scope: list[str],
    surface_area: list[str],
) -> uuid.UUID:
    actor_id = _fixture_uuid(root)
    workspace_id = _fixture_uuid(root + 1)
    project_id = _fixture_uuid(root + 2)
    dependency_task_id = _fixture_uuid(root + 3)
    dependency_run_id = _fixture_uuid(root + 4)
    decision_id = _fixture_uuid(root + 5)
    target_task_id = _fixture_uuid(root + 6)
    target_run_id = _fixture_uuid(root + 7)
    artifact_id = _fixture_uuid(root + 8)
    learning_id = _fixture_uuid(root + 9)

    create_actor(db_session, _actor_payload(actor_id))
    create_workspace(db_session, _workspace_payload(workspace_id))
    create_project(db_session, _project_payload(project_id, workspace_id))
    create_task(
        db_session,
        _task_for_case(
            task_id=dependency_task_id,
            project_id=project_id,
            title=dependency_title,
            spec=dependency_spec,
            created_at="2026-04-20T00:00:00+00:00",
            file_scope=file_scope,
            surface_area=surface_area,
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
        _decision_for_case(
            decision_id=decision_id,
            task_id=dependency_task_id,
            run_id=dependency_run_id,
            actor_id=actor_id,
            summary=decision_summary,
            decided_at="2026-04-20T00:02:00+00:00",
        ),
    )
    create_task(
        db_session,
        _task_for_case(
            task_id=target_task_id,
            project_id=project_id,
            title=target_title,
            spec=target_spec,
            created_at="2026-04-20T00:03:00+00:00",
            file_scope=file_scope,
            surface_area=surface_area,
        ),
    )
    create_run(
        db_session,
        _run_payload(
            run_id=target_run_id,
            task_id=target_task_id,
            actor_id=actor_id,
            started_at="2026-04-20T00:04:00+00:00",
        ),
    )
    create_artifact(
        db_session,
        _artifact_for_case(
            artifact_id=artifact_id,
            task_id=target_task_id,
            run_id=target_run_id,
            uri=artifact_uri,
            created_at="2026-04-20T00:05:00+00:00",
        ),
    )
    create_learning(
        db_session,
        _learning_for_case(
            learning_id=learning_id,
            task_id=dependency_task_id,
            title=learning_title,
            evidence=learning_evidence,
            created_at="2026-04-20T00:06:00+00:00",
        ),
    )
    create_edge(
        db_session,
        _edge_payload(
            edge_id=_fixture_uuid(root + 10),
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
            edge_id=_fixture_uuid(root + 11),
            src_entity_type="decision",
            src_id=decision_id,
            dst_entity_type="task",
            dst_id=dependency_task_id,
            relation=EdgeRelation.TRIGGERED,
        ),
    )
    return target_task_id


def _seed_graph_core_packet(db_session: Session) -> uuid.UUID:
    return _seed_case(
        db_session,
        root=0x1100,
        target_title="Compile the packet graph core",
        target_spec=(
            "## Goal\nCompile the graph-first packet core.\n\n"
            "## Open Questions\n"
            "- Should packet invalidation flush downstream prefetched packets?\n"
            "- Who owns packet TTL defaults?\n"
        ),
        dependency_title="Finalize packet graph traversal",
        dependency_spec="Dependency task for graph traversal in the packet compiler.",
        decision_summary="Keep graph traversal as the packet hot path",
        learning_title="Graph-first packet assembly keeps the hot path predictable",
        learning_evidence="tests/packets/expected/graph_core_packet.json",
        artifact_uri="artifacts/diffs/graph-core-packet.patch",
        file_scope=[
            "apps/api/src/agenticqueue_api/compiler.py",
            "task_types/coding-task.schema.json",
        ],
        surface_area=["packet", "compiler", "graph"],
    )


def _seed_transport_alignment_packet(db_session: Session) -> uuid.UUID:
    return _seed_case(
        db_session,
        root=0x1200,
        target_title="Align packet transport surfaces",
        target_spec=(
            "## Goal\nKeep packet transport surfaces field-identical.\n\n"
            "## Open Questions\n"
            "- Should the REST and CLI surfaces share one renderer?\n"
        ),
        dependency_title="Close packet transport parity gaps",
        dependency_spec="Dependency task for packet transport parity.",
        decision_summary="Keep packet transport outputs field-identical",
        learning_title="Packet transports stay calmer when they share one shape",
        learning_evidence="tests/packets/expected/transport_alignment_packet.json",
        artifact_uri="artifacts/diffs/packet-transport-parity.patch",
        file_scope=[
            "apps/api/src/agenticqueue_api/routers/packets.py",
            "apps/api/src/agenticqueue_api/mcp/packet_tools.py",
        ],
        surface_area=["packet", "transport", "parity"],
    )


def _seed_dogfood_checkpoint_packet(db_session: Session) -> uuid.UUID:
    return _seed_case(
        db_session,
        root=0x1300,
        target_title="Prepare DOGFOOD CHECKPOINT 1 packet fixture",
        target_spec=(
            "## Goal\nSeed DOGFOOD CHECKPOINT 1 fixtures for dual-write observer mode.\n\n"
            "## Open Questions\n"
            "- Which mmmmm project should enter observer mode first?\n"
        ),
        dependency_title="Stage dual-write observer packet",
        dependency_spec="Dependency task for the first dogfood dual-write checkpoint.",
        decision_summary="Keep DOGFOOD CHECKPOINT 1 in observer mode first",
        learning_title="Seed dogfood fixtures before enabling dual-write",
        learning_evidence="tests/packets/expected/dogfood_checkpoint_packet.json",
        artifact_uri="artifacts/diffs/dogfood-checkpoint-1.patch",
        file_scope=[
            "examples/tasks/coding/01-add-endpoint.json",
            "task_types/coding-task.policy.yaml",
            "task_types/coding-task.schema.json",
        ],
        surface_area=["dogfood", "observer-mode", "packet"],
    )


CASES: list[tuple[str, Callable[[Session], uuid.UUID]]] = [
    ("graph_core_packet", _seed_graph_core_packet),
    ("transport_alignment_packet", _seed_transport_alignment_packet),
    ("dogfood_checkpoint_packet", _seed_dogfood_checkpoint_packet),
]


def _expected_path(case_name: str) -> Path:
    return EXPECTED_DIR / f"{case_name}.json"


def _canonical_packet(packet: dict[str, object]) -> str:
    return json.dumps(packet, indent=2, sort_keys=True) + "\n"


def _golden_diff(case_name: str, expected_text: str, actual_text: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            expected_text.splitlines(),
            actual_text.splitlines(),
            fromfile=str(_expected_path(case_name)),
            tofile=f"compiled:{case_name}",
            lineterm="",
        )
    )


@pytest.mark.parametrize(
    ("case_name", "seed_fixture"),
    CASES,
    ids=[case_name for case_name, _ in CASES],
)
def test_compiled_packet_matches_checked_in_golden(
    db_session: Session,
    case_name: str,
    seed_fixture: Callable[[Session], uuid.UUID],
) -> None:
    task_id = seed_fixture(db_session)

    packet = compile_packet(db_session, task_id)

    expected_text = _expected_path(case_name).read_text(encoding="utf-8")
    actual_text = _canonical_packet(packet)
    assert actual_text == expected_text, _golden_diff(
        case_name, expected_text, actual_text
    )


def test_golden_diff_renders_unified_diff_headers() -> None:
    diff = _golden_diff("graph_core_packet", '{\n  "a": 1\n}\n', '{\n  "a": 2\n}\n')

    assert f"--- {_expected_path('graph_core_packet')}" in diff
    assert "+++ compiled:graph_core_packet" in diff
    assert "@@" in diff
