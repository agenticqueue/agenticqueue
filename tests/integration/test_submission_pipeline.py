from __future__ import annotations

import copy
import json
from pathlib import Path
import uuid
from typing import Any

from fastapi.testclient import TestClient
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.learnings.draft import DraftLearningRecord
from agenticqueue_api.middleware.idempotency import (
    IDEMPOTENCY_KEY_HEADER,
    IDEMPOTENCY_REPLAYED_HEADER,
)
from agenticqueue_api.models import (
    ArtifactRecord,
    AuditLogRecord,
    CapabilityKey,
    EdgeRecord,
    EdgeRelation,
    IdempotencyKeyRecord,
    PacketVersionRecord,
    RunRecord,
    TaskRecord,
)
from tests.aq.test_packet_mcp import (
    _seed_task_with_token,
    clean_database,
    engine,
    session_factory,
)

__all__ = ["clean_database", "engine", "session_factory"]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _example_contract() -> dict[str, Any]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_submission_artifacts(artifact_root: Path) -> None:
    _write(
        artifact_root / "artifacts" / "diffs" / "aq-52.patch",
        "@@ /v1/tasks/{id}\n+ test_get_task_returns_200\n",
    )
    _write(
        artifact_root / "artifacts" / "tests" / "aq-52-pytest.txt",
        "test_get_task_returns_200\ntest_missing_task_returns_404\n4 passed in 0.15s\n",
    )


def _valid_submission() -> dict[str, Any]:
    contract = _example_contract()
    return {
        "output": copy.deepcopy(contract["output"]),
        "dod_results": [
            {"item": item, "checked": True} for item in contract["dod_checklist"]
        ],
        "had_failure": False,
        "had_block": False,
        "had_retry": False,
    }


def _headers(token: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        IDEMPOTENCY_KEY_HEADER: idempotency_key or str(uuid.uuid4()),
    }


def _count_rows(
    session: Session,
    model: type[object],
    *conditions: sa.ColumnElement[bool],
) -> int:
    statement = sa.select(sa.func.count()).select_from(model)
    for condition in conditions:
        statement = statement.where(condition)
    return int(session.scalar(statement) or 0)


def test_submit_route_rejects_invalid_payload_without_side_effects(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    app = create_app(session_factory=session_factory, artifact_root=tmp_path)
    _, _, task_id, token = _seed_task_with_token(
        session_factory,
        handle="submission-invalid",
        grant_capabilities=(
            CapabilityKey.RUN_TESTS,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
        ),
        task_state="in_progress",
        claimed_by_seed_actor=True,
        contract=_example_contract(),
    )
    payload = _valid_submission()
    output = dict(payload["output"])
    output.pop("diff_url")
    payload["output"] = output

    with TestClient(app) as client:
        response = client.post(
            f"/v1/tasks/{task_id}/submit",
            headers=_headers(token),
            json=payload,
        )

    assert response.status_code == 422
    assert response.json()["message"] == "Task submission failed validation"

    with session_factory() as session:
        task = session.get(TaskRecord, task_id)

        assert task is not None
        assert task.state == "in_progress"
        assert task.claimed_by_actor_id is not None
        assert _count_rows(session, RunRecord) == 0
        assert _count_rows(session, ArtifactRecord) == 0
        assert _count_rows(session, EdgeRecord) == 0
        assert _count_rows(session, DraftLearningRecord) == 0
        assert _count_rows(session, PacketVersionRecord) == 0
        assert (
            _count_rows(
                session,
                AuditLogRecord,
                AuditLogRecord.action == "JOB_SUBMITTED",
            )
            == 0
        )


def test_submit_route_persists_artifacts_and_replays_idempotently(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    _write_submission_artifacts(tmp_path)
    app = create_app(session_factory=session_factory, artifact_root=tmp_path)
    _, _, task_id, token = _seed_task_with_token(
        session_factory,
        handle="submission-valid",
        grant_capabilities=(
            CapabilityKey.RUN_TESTS,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
        ),
        task_state="in_progress",
        claimed_by_seed_actor=True,
        contract=_example_contract(),
    )
    payload = _valid_submission()
    key = str(uuid.uuid4())
    headers = _headers(token, idempotency_key=key)

    with TestClient(app) as client:
        first = client.post(
            f"/v1/tasks/{task_id}/submit",
            headers=headers,
            json=payload,
        )
        second = client.post(
            f"/v1/tasks/{task_id}/submit",
            headers=headers,
            json=payload,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.headers[IDEMPOTENCY_REPLAYED_HEADER] == "true"
    assert second.json() == first.json()

    body = first.json()
    assert body["task"]["state"] == "validated"
    assert body["task"]["claimed_by_actor_id"] is None
    assert body["run"]["status"] == "validated"
    assert body["next_action"] == "await_human_approval"
    assert [item["state"] for item in body["transitions"]] == [
        "submitted",
        "validated",
    ]
    assert len(body["artifacts"]) == 2
    assert len(body["learning_drafts"]) == 1
    assert body["dod_report"]["checked_count"] == 3

    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        replay_record = session.get(IdempotencyKeyRecord, key)
        audit_rows = session.scalars(
            sa.select(AuditLogRecord)
            .where(
                AuditLogRecord.entity_type == "task",
                AuditLogRecord.entity_id == task_id,
                AuditLogRecord.action == "JOB_SUBMITTED",
            )
            .order_by(AuditLogRecord.created_at.asc(), AuditLogRecord.id.asc())
        ).all()

        assert task is not None
        assert task.state == "validated"
        assert task.claimed_by_actor_id is None
        assert _count_rows(session, RunRecord, RunRecord.task_id == task_id) == 1
        assert (
            _count_rows(session, ArtifactRecord, ArtifactRecord.task_id == task_id) == 2
        )
        assert (
            _count_rows(
                session,
                EdgeRecord,
                EdgeRecord.src_entity_type == "task",
                EdgeRecord.src_id == task_id,
                EdgeRecord.relation == EdgeRelation.PRODUCED,
            )
            == 2
        )
        assert (
            _count_rows(
                session,
                DraftLearningRecord,
                DraftLearningRecord.task_id == task_id,
            )
            == 1
        )
        assert (
            _count_rows(
                session,
                PacketVersionRecord,
                PacketVersionRecord.task_id == task_id,
            )
            == 1
        )
        assert replay_record is not None
        assert replay_record.replay_count == 1
        assert len(audit_rows) == 1
        assert audit_rows[0].after == {
            "run_id": body["run"]["id"],
            "task_state": "validated",
            "artifact_count": 2,
            "learning_draft_count": 1,
            "next_action": "await_human_approval",
        }


def test_escrow_unlock_route_releases_claim_and_records_reason(
    session_factory: sessionmaker[Session],
) -> None:
    app = create_app(session_factory=session_factory)
    admin_id, _, task_id, token = _seed_task_with_token(
        session_factory,
        handle="submission-unlock-admin",
        actor_type="admin",
        task_state="in_progress",
        claimed_by_seed_actor=True,
        contract=_example_contract(),
    )
    reason = "Supervisor reclaimed stalled task."

    with TestClient(app) as client:
        response = client.post(
            f"/v1/tasks/{task_id}/escrow-unlock",
            headers=_headers(token),
            json={"reason": reason},
        )

    assert response.status_code == 200
    assert response.json()["state"] == "todo"
    assert response.json()["claimed_by_actor_id"] is None
    assert response.json()["claimed_at"] is None

    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        audit_rows = session.scalars(
            sa.select(AuditLogRecord)
            .where(
                AuditLogRecord.entity_type == "task",
                AuditLogRecord.entity_id == task_id,
                AuditLogRecord.action == "ESCROW_FORCE_UNLOCKED",
            )
            .order_by(AuditLogRecord.created_at.asc(), AuditLogRecord.id.asc())
        ).all()

        assert task is not None
        assert task.state == "todo"
        assert task.claimed_by_actor_id is None
        assert task.claimed_at is None
        assert len(audit_rows) == 1
        assert audit_rows[0].actor_id == admin_id
        assert audit_rows[0].after == {"state": "todo", "reason": reason}
