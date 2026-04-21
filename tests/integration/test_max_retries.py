from __future__ import annotations

import datetime as dt
from pathlib import Path
import uuid

from fastapi.testclient import TestClient
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.models import (
    AuditLogRecord,
    CapabilityKey,
    PolicyRecord,
    TaskRecord,
)
from agenticqueue_api.task_retry import attempt_metric_value, reset_attempt_metrics
from tests.aq.test_packet_mcp import (
    _seed_task_with_token,
    clean_database,
    engine,
    session_factory,
)
from tests.integration.test_submission_pipeline import _valid_submission

__all__ = ["clean_database", "engine", "session_factory"]


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": str(uuid.uuid4()),
    }


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_submission_artifacts(artifact_root: Path) -> None:
    _write(
        artifact_root / "artifacts" / "diffs" / "aq-180.patch",
        "@@ /v1/tasks/{id}\n+ max retries path\n",
    )
    _write(
        artifact_root / "artifacts" / "tests" / "aq-180-pytest.txt",
        "max retries\n",
    )


def _invalid_submission() -> dict[str, object]:
    payload = _valid_submission()
    payload["output"].pop("diff_url")
    return payload


def _attach_task_policy(
    session_factory: sessionmaker[Session],
    task_id: uuid.UUID,
    *,
    max_attempts: int,
) -> None:
    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        assert task is not None
        policy = PolicyRecord(
            workspace_id=None,
            name=f"max-attempts-{max_attempts}",
            version="1.0.0",
            hitl_required=True,
            autonomy_tier=3,
            capabilities=[],
            body={"max_attempts_per_task_type": {"coding-task": max_attempts}},
        )
        session.add(policy)
        session.flush()
        task.policy_id = policy.id
        session.commit()


def _resume_task_for_retry(
    session_factory: sessionmaker[Session],
    task_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
) -> None:
    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        assert task is not None
        task.state = "in_progress"
        task.claimed_by_actor_id = actor_id
        task.claimed_at = dt.datetime.now(dt.UTC)
        session.commit()


def test_three_failures_move_task_to_dlq_and_increment_metric(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    reset_attempt_metrics()
    app = create_app(session_factory=session_factory, artifact_root=tmp_path)
    actor_id, _, task_id, token = _seed_task_with_token(
        session_factory,
        handle="max-retries-default",
        grant_capabilities=(
            CapabilityKey.RUN_TESTS,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
        ),
        task_state="in_progress",
        claimed_by_seed_actor=True,
    )

    with TestClient(app) as client:
        for expected_attempt in (1, 2, 3):
            response = client.post(
                f"/v1/tasks/{task_id}/submit",
                headers=_headers(token),
                json=_invalid_submission(),
            )
            assert response.status_code == 422
            assert response.json()["details"]["attempt_count"] == expected_attempt
            if expected_attempt < 3:
                _resume_task_for_retry(
                    session_factory,
                    task_id,
                    actor_id=actor_id,
                )

    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        dlq_rows = session.scalars(
            sa.select(AuditLogRecord)
            .where(
                AuditLogRecord.entity_type == "task",
                AuditLogRecord.entity_id == task_id,
                AuditLogRecord.action == "JOB_DLQ_ENTERED",
            )
            .order_by(AuditLogRecord.created_at.asc(), AuditLogRecord.id.asc())
        ).all()

        assert task is not None
        assert task.state == "dlq"
        assert task.attempt_count == 3
        assert task.last_failure is not None
        assert len(dlq_rows) == 1
        assert dlq_rows[0].after["attempt_count"] == 3
        assert dlq_rows[0].after["max_attempts"] == 3

    assert attempt_metric_value("dlq") == 1


def test_dlq_task_rejects_reclaim_attempt_with_conflict(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    reset_attempt_metrics()
    app = create_app(session_factory=session_factory, artifact_root=tmp_path)
    _, _, task_id, token = _seed_task_with_token(
        session_factory,
        handle="max-retries-claim",
        grant_capabilities=(CapabilityKey.RUN_TESTS,),
        task_state="dlq",
    )

    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        assert task is not None
        task.attempt_count = 3
        task.last_failure = {"error_code": "validation_failed", "message": "boom"}
        session.commit()

    with TestClient(app) as client:
        response = client.post(
            f"/v1/tasks/{task_id}/claim",
            headers=_headers(token),
        )

    assert response.status_code == 409
    assert response.json()["error_code"] == "in_dlq"


def test_policy_override_moves_task_to_dlq_after_first_failure(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    reset_attempt_metrics()
    app = create_app(session_factory=session_factory, artifact_root=tmp_path)
    _write_submission_artifacts(tmp_path)
    _, _, task_id, token = _seed_task_with_token(
        session_factory,
        handle="max-retries-override",
        grant_capabilities=(
            CapabilityKey.RUN_TESTS,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
        ),
        task_state="in_progress",
        claimed_by_seed_actor=True,
        token_scopes=("task:read",),
    )
    _attach_task_policy(session_factory, task_id, max_attempts=1)

    with TestClient(app) as client:
        response = client.post(
            f"/v1/tasks/{task_id}/submit",
            headers=_headers(token),
            json=_invalid_submission(),
        )
        fetch = client.get(f"/v1/tasks/{task_id}", headers=_headers(token))

    assert response.status_code == 422
    assert response.json()["details"]["attempt_count"] == 1
    assert response.json()["details"]["max_attempts"] == 1
    assert response.json()["details"]["remaining_attempts"] == 0
    assert response.json()["details"]["task_state"] == "dlq"

    assert fetch.status_code == 200
    assert fetch.json()["attempt_count"] == 1
    assert fetch.json()["max_attempts"] == 1
    assert fetch.json()["remaining_attempts"] == 0
    assert fetch.json()["state"] == "dlq"
