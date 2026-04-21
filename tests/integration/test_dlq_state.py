from __future__ import annotations

from pathlib import Path
import uuid

from fastapi.testclient import TestClient
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.mcp import build_agenticqueue_mcp
from agenticqueue_api.models import AuditLogRecord, CapabilityKey
from agenticqueue_api.task_retry import reset_attempt_metrics
from tests.aq.test_packet_mcp import (
    _mcp_call,
    _seed_task_with_token,
    clean_database,
    engine,
    session_factory,
)
from tests.integration.test_max_retries import _resume_task_for_retry
from tests.integration.test_submission_pipeline import _valid_submission

__all__ = ["clean_database", "engine", "session_factory"]


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": str(uuid.uuid4()),
    }


def _invalid_submission() -> dict[str, object]:
    payload = _valid_submission()
    payload["output"].pop("diff_url")
    return payload


def _drive_task_into_dlq(
    client: TestClient,
    *,
    session_factory: sessionmaker[Session],
    actor_id: uuid.UUID,
    task_id: uuid.UUID,
    token: str,
) -> None:
    for attempt in (1, 2, 3):
        response = client.post(
            f"/v1/tasks/{task_id}/submit",
            headers=_headers(token),
            json=_invalid_submission(),
        )
        assert response.status_code == 422
        assert response.json()["details"]["attempt_count"] == attempt
        if attempt < 3:
            _resume_task_for_retry(
                session_factory,
                task_id,
                actor_id=actor_id,
            )


def test_dlq_state_surfaces_via_rest_and_mcp_and_blocks_reclaim(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    reset_attempt_metrics()
    app = create_app(session_factory=session_factory, artifact_root=tmp_path)
    actor_id, _, task_id, token = _seed_task_with_token(
        session_factory,
        handle="dlq-surface",
        grant_capabilities=(
            CapabilityKey.RUN_TESTS,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
        ),
        task_state="in_progress",
        claimed_by_seed_actor=True,
        token_scopes=("task:read",),
    )
    mcp = build_agenticqueue_mcp(app=app, session_factory=session_factory)

    with TestClient(app) as client:
        _drive_task_into_dlq(
            client,
            session_factory=session_factory,
            actor_id=actor_id,
            task_id=task_id,
            token=token,
        )
        rest_list = client.get(
            "/v1/tasks",
            headers=_headers(token),
            params={"state": "dlq"},
        )
        claim = client.post(
            f"/v1/tasks/{task_id}/claim",
            headers=_headers(token),
        )

    assert rest_list.status_code == 200
    rest_items = rest_list.json()
    assert any(item["id"] == str(task_id) for item in rest_items)
    assert all(item["state"] == "dlq" for item in rest_items)

    mcp_list = _mcp_call(
        mcp,
        "list_jobs",
        {"token": token, "filters": {"state": "dlq"}},
    )
    mcp_items = mcp_list if isinstance(mcp_list, list) else mcp_list.get("items", [])
    assert any(item["id"] == str(task_id) for item in mcp_items)
    assert all(item["state"] == "dlq" for item in mcp_items)

    assert claim.status_code == 409
    assert claim.json()["error_code"] == "in_dlq"

    with session_factory() as session:
        audit_rows = session.scalars(
            sa.select(AuditLogRecord)
            .where(
                AuditLogRecord.entity_type == "task",
                AuditLogRecord.entity_id == task_id,
                AuditLogRecord.action == "JOB_DLQ_ENTERED",
            )
            .order_by(AuditLogRecord.created_at.asc(), AuditLogRecord.id.asc())
        ).all()

    assert len(audit_rows) == 1
    after = audit_rows[0].after
    assert after is not None
    assert after["state"] == "dlq"
