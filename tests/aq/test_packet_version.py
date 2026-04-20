from __future__ import annotations

# ruff: noqa: E402

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

pytest_plugins = ["tests.aq.test_packet_assembler"]

from agenticqueue_api.compiler import (
    assemble_packet,
    compile_packet,
    get_packet_by_hash,
)
from agenticqueue_api.models import PacketVersionRecord, RunModel
from agenticqueue_api.packet_versions import (
    get_current_packet_version,
    packet_content_hash,
)
from agenticqueue_api.repo import create_run, get_run
from tests.aq.test_packet_assembler import (
    _actor_payload,
    _project_payload,
    _seed_graph_fixture,
    _task_payload,
    _workspace_payload,
    create_actor,
    create_project,
    create_task,
    create_workspace,
)


def test_packet_version_hash_is_stable_across_reassemblies(
    db_session: Session,
) -> None:
    target_task_id = _seed_graph_fixture(db_session)

    first_packet = assemble_packet(db_session, target_task_id)
    second_packet = assemble_packet(db_session, target_task_id)

    assert packet_content_hash(first_packet) == packet_content_hash(second_packet)
    assert first_packet.packet_version_id == second_packet.packet_version_id


def test_compile_packet_persists_and_retrieves_by_hash(
    db_session: Session,
) -> None:
    target_task_id = _seed_graph_fixture(db_session)

    assert get_current_packet_version(db_session, target_task_id) is None

    compiled_packet = compile_packet(db_session, target_task_id)
    repeated_packet = compile_packet(db_session, target_task_id)
    packet_hash = packet_content_hash(compiled_packet)

    current = get_current_packet_version(db_session, target_task_id)
    assert current is not None
    assert current.packet_hash == packet_hash
    assert current.payload == compiled_packet
    assert repeated_packet == compiled_packet

    replayed = get_packet_by_hash(db_session, packet_hash)
    assert replayed == compiled_packet

    row_count = db_session.scalar(
        sa.select(sa.func.count()).select_from(PacketVersionRecord)
    )
    assert row_count == 1


def test_submission_run_references_packet_version_id(
    db_session: Session,
) -> None:
    actor_id = uuid.UUID("00000000-0000-0000-0000-000000000901")
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000902")
    project_id = uuid.UUID("00000000-0000-0000-0000-000000000903")
    task_id = uuid.UUID("00000000-0000-0000-0000-000000000904")
    run_id = uuid.UUID("00000000-0000-0000-0000-000000000905")

    create_actor(db_session, _actor_payload(actor_id))
    create_workspace(db_session, _workspace_payload(workspace_id))
    create_project(db_session, _project_payload(project_id, workspace_id))
    create_task(
        db_session,
        _task_payload(
            task_id=task_id,
            project_id=project_id,
            title="Persist packet version for submission",
            spec="## Goal\nPersist and reference the exact packet version.",
            created_at="2026-04-20T00:00:00+00:00",
        ),
    )

    packet = compile_packet(db_session, task_id)
    create_run(
        db_session,
        RunModel.model_validate(
            {
                "id": str(run_id),
                "task_id": str(task_id),
                "packet_version_id": packet["packet_version_id"],
                "actor_id": str(actor_id),
                "status": "submitted",
                "started_at": "2026-04-20T00:00:00+00:00",
                "ended_at": "2026-04-20T00:01:00+00:00",
                "summary": "Submission captured",
                "details": {"submitted_at": "2026-04-20T00:01:00+00:00"},
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:01:00+00:00",
            }
        ),
    )

    stored_run = get_run(db_session, run_id)
    assert stored_run is not None
    assert stored_run.packet_version_id == uuid.UUID(packet["packet_version_id"])


def test_get_packet_by_hash_returns_none_for_unknown_hash(
    db_session: Session,
) -> None:
    assert (
        get_packet_by_hash(
            db_session,
            "0" * 64,
        )
        is None
    )
