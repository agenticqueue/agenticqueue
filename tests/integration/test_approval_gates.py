from __future__ import annotations

from pathlib import Path
import uuid

from fastapi.testclient import TestClient
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker
import yaml

from agenticqueue_api.app import create_app
from agenticqueue_api.models import AuditLogRecord, CapabilityKey, TaskRecord
from agenticqueue_api.task_type_registry import TaskTypeRegistry
from tests.aq.test_packet_mcp import (
    _seed_task_with_token,
    clean_database,
    engine,
    session_factory,
)
from tests.integration.test_submission_pipeline import (
    _valid_submission,
    _write_submission_artifacts,
)

__all__ = ["clean_database", "engine", "session_factory"]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": str(uuid.uuid4()),
    }


def _registry_with_hitl(tmp_path: Path, *, hitl_required: bool) -> TaskTypeRegistry:
    source_dir = _repo_root() / "task_types"
    target_dir = tmp_path / f"task-types-{'hitl-on' if hitl_required else 'hitl-off'}"
    target_dir.mkdir(parents=True, exist_ok=True)

    schema_path = source_dir / "coding-task.schema.json"
    policy_path = source_dir / "coding-task.policy.yaml"
    (target_dir / schema_path.name).write_text(
        schema_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy["hitl_required"] = hitl_required
    (target_dir / policy_path.name).write_text(
        yaml.safe_dump(policy, sort_keys=False),
        encoding="utf-8",
    )

    registry = TaskTypeRegistry(target_dir)
    registry.load()
    return registry


def test_hitl_on_task_blocks_until_approved(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    _write_submission_artifacts(tmp_path)
    app = create_app(session_factory=session_factory, artifact_root=tmp_path)
    _, _, task_id, token = _seed_task_with_token(
        session_factory,
        handle="approval-hitl-on-approve",
        grant_capabilities=(
            CapabilityKey.RUN_TESTS,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
        ),
        task_state="in_progress",
        claimed_by_seed_actor=True,
    )

    with TestClient(app) as client:
        submitted = client.post(
            f"/v1/tasks/{task_id}/submit",
            headers=_headers(token),
            json=_valid_submission(),
        )
        approved = client.post(
            f"/v1/tasks/{task_id}/approve",
            headers=_headers(token),
            json={"reason": "ship it"},
        )

    assert submitted.status_code == 200
    assert submitted.json()["task"]["state"] == "validated"
    assert submitted.json()["next_action"] == "await_human_approval"

    assert approved.status_code == 200
    assert approved.json()["state"] == "done"
    assert approved.json()["claimed_by_actor_id"] is None

    with session_factory() as session:
        audit_rows = session.scalars(
            sa.select(AuditLogRecord)
            .where(
                AuditLogRecord.entity_type == "task",
                AuditLogRecord.entity_id == task_id,
                AuditLogRecord.action == "JOB_APPROVED",
            )
            .order_by(AuditLogRecord.created_at.asc(), AuditLogRecord.id.asc())
        ).all()

    assert len(audit_rows) == 1
    assert audit_rows[0].after == {
        "state": "done",
        "mode": "human",
        "reason": "ship it",
    }


def test_hitl_on_reject_loops_back_to_queued(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    _write_submission_artifacts(tmp_path)
    app = create_app(session_factory=session_factory, artifact_root=tmp_path)
    _, _, task_id, token = _seed_task_with_token(
        session_factory,
        handle="approval-hitl-on-reject",
        grant_capabilities=(
            CapabilityKey.RUN_TESTS,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
        ),
        task_state="in_progress",
        claimed_by_seed_actor=True,
    )

    with TestClient(app) as client:
        submitted = client.post(
            f"/v1/tasks/{task_id}/submit",
            headers=_headers(token),
            json=_valid_submission(),
        )
        rejected = client.post(
            f"/v1/tasks/{task_id}/reject",
            headers=_headers(token),
            json={"reason": "needs fixes"},
        )

    assert submitted.status_code == 200
    assert submitted.json()["task"]["state"] == "validated"

    assert rejected.status_code == 200
    assert rejected.json()["state"] == "queued"
    assert rejected.json()["claimed_by_actor_id"] is None

    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        audit_rows = session.scalars(
            sa.select(AuditLogRecord)
            .where(
                AuditLogRecord.entity_type == "task",
                AuditLogRecord.entity_id == task_id,
                AuditLogRecord.action == "JOB_REJECTED",
            )
            .order_by(AuditLogRecord.created_at.asc(), AuditLogRecord.id.asc())
        ).all()

    assert task is not None
    assert task.state == "queued"
    assert len(audit_rows) == 1
    assert audit_rows[0].after == {
        "state": "queued",
        "intermediate_state": "rejected",
        "attempt_count": 1,
        "reason": "needs fixes",
    }


def test_hitl_off_auto_approves_inline_without_followup_endpoint(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    _write_submission_artifacts(tmp_path)
    registry = _registry_with_hitl(tmp_path, hitl_required=False)
    app = create_app(
        session_factory=session_factory,
        task_type_registry=registry,
        artifact_root=tmp_path,
    )
    _, _, task_id, token = _seed_task_with_token(
        session_factory,
        handle="approval-hitl-off",
        grant_capabilities=(
            CapabilityKey.RUN_TESTS,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
        ),
        task_state="in_progress",
        claimed_by_seed_actor=True,
    )

    with TestClient(app) as client:
        submitted = client.post(
            f"/v1/tasks/{task_id}/submit",
            headers=_headers(token),
            json=_valid_submission(),
        )

    body = submitted.json()
    assert submitted.status_code == 200
    assert body["task"]["state"] == "done"
    assert body["run"]["status"] == "done"
    assert body["next_action"] == "done"
    assert [item["state"] for item in body["transitions"]] == [
        "submitted",
        "validated",
        "done",
    ]

    with session_factory() as session:
        audit_rows = session.scalars(
            sa.select(AuditLogRecord)
            .where(
                AuditLogRecord.entity_type == "task",
                AuditLogRecord.entity_id == task_id,
                AuditLogRecord.action == "JOB_APPROVED",
            )
            .order_by(AuditLogRecord.created_at.asc(), AuditLogRecord.id.asc())
        ).all()

    assert len(audit_rows) == 1
    assert audit_rows[0].after == {"state": "done", "mode": "automatic"}


def test_switching_policy_packs_changes_behavior_without_code_change(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    _write_submission_artifacts(tmp_path)
    on_app = create_app(session_factory=session_factory, artifact_root=tmp_path)
    off_registry = _registry_with_hitl(tmp_path, hitl_required=False)
    off_app = create_app(
        session_factory=session_factory,
        task_type_registry=off_registry,
        artifact_root=tmp_path,
    )

    _, _, on_task_id, on_token = _seed_task_with_token(
        session_factory,
        handle="approval-policy-pack-on",
        grant_capabilities=(
            CapabilityKey.RUN_TESTS,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
        ),
        task_state="in_progress",
        claimed_by_seed_actor=True,
    )
    _, _, off_task_id, off_token = _seed_task_with_token(
        session_factory,
        handle="approval-policy-pack-off",
        grant_capabilities=(
            CapabilityKey.RUN_TESTS,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
        ),
        task_state="in_progress",
        claimed_by_seed_actor=True,
    )

    with TestClient(on_app) as on_client:
        on_response = on_client.post(
            f"/v1/tasks/{on_task_id}/submit",
            headers=_headers(on_token),
            json=_valid_submission(),
        )

    with TestClient(off_app) as off_client:
        off_response = off_client.post(
            f"/v1/tasks/{off_task_id}/submit",
            headers=_headers(off_token),
            json=_valid_submission(),
        )

    assert on_response.status_code == 200
    assert off_response.status_code == 200
    assert on_response.json()["task"]["state"] == "validated"
    assert on_response.json()["next_action"] == "await_human_approval"
    assert off_response.json()["task"]["state"] == "done"
    assert off_response.json()["next_action"] == "done"
