from __future__ import annotations

from pathlib import Path
import uuid

from fastapi.testclient import TestClient
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.models import (
    AuditLogRecord,
    ArtifactRecord,
    CapabilityKey,
    LearningRecord,
    PacketVersionRecord,
    PolicyRecord,
    RunRecord,
    TaskModel,
    TaskRecord,
)
from agenticqueue_api.models.edge import EdgeModel, EdgeRelation
from agenticqueue_api.repo import claim_next, create_edge, create_task
from tests.aq.test_packet_mcp import (
    _seed_task_with_token,
    clean_database,
    engine,
    session_factory,
)
from tests.integration.test_submission_pipeline import (
    _example_contract,
    _valid_submission,
    _write_submission_artifacts,
)

__all__ = ["clean_database", "engine", "session_factory"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _post_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": str(uuid.uuid4()),
    }


def _issue_learning_token(
    session_factory: sessionmaker[Session],
    *,
    actor_id: uuid.UUID,
) -> str:
    with session_factory() as session:
        _, token = issue_api_token(
            session,
            actor_id=actor_id,
            scopes=["learning:read", "learning:write"],
            expires_at=None,
        )
        session.commit()
        return token


def _attach_task_policy(
    session_factory: sessionmaker[Session],
    task_id: uuid.UUID,
    *,
    name: str,
    hitl_required: bool,
) -> None:
    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        assert task is not None
        policy = PolicyRecord(
            workspace_id=None,
            name=name,
            version="1.0.0",
            hitl_required=hitl_required,
            autonomy_tier=3,
            capabilities=[],
            body={},
        )
        session.add(policy)
        session.flush()
        task.policy_id = policy.id
        session.commit()


def _create_blocked_dependent(
    session_factory: sessionmaker[Session],
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    upstream_task_id: uuid.UUID,
    handle: str,
) -> uuid.UUID:
    contract = _example_contract()
    with session_factory() as session:
        downstream = create_task(
            session,
            TaskModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project_id),
                    "task_type": "coding-task",
                    "title": f"{handle} downstream task",
                    "state": "blocked",
                    "description": "Waits on the upstream lifecycle fixture.",
                    "contract": contract,
                    "definition_of_done": contract["dod_checklist"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        create_edge(
            session,
            EdgeModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "src_entity_type": "task",
                    "src_id": str(downstream.id),
                    "dst_entity_type": "task",
                    "dst_id": str(upstream_task_id),
                    "relation": EdgeRelation.DEPENDS_ON.value,
                    "metadata": {},
                    "created_by": str(actor_id),
                    "created_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        session.commit()
        return downstream.id


def _claim_task_in_progress(
    session_factory: sessionmaker[Session],
    *,
    actor_id: uuid.UUID,
    task_id: uuid.UUID,
) -> None:
    claim_label = f"claim:{task_id}"
    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        assert task is not None
        task.labels = list(dict.fromkeys([*(task.labels or []), claim_label]))
        session.flush()
        claimed = claim_next(
            session,
            actor_id=actor_id,
            labels=[claim_label],
            claim_states=["queued"],
            claimed_state="in_progress",
        )
        session.commit()
    assert claimed is not None
    assert claimed.id == task_id
    assert claimed.state == "in_progress"


def test_hitl_off_full_lifecycle_confirms_learning_and_unblocks_next_task(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    _write_submission_artifacts(tmp_path)
    actor_id, project_id, task_id, _ = _seed_task_with_token(
        session_factory,
        handle="lifecycle-hitl-off",
        grant_query_graph=True,
        grant_capabilities=(
            CapabilityKey.RUN_TESTS,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
            CapabilityKey.WRITE_LEARNING,
        ),
        task_state="queued",
        contract=_example_contract(),
    )
    token = _issue_learning_token(session_factory, actor_id=actor_id)
    _attach_task_policy(
        session_factory,
        task_id,
        name="lifecycle-hitl-off-policy",
        hitl_required=False,
    )
    downstream_task_id = _create_blocked_dependent(
        session_factory,
        project_id=project_id,
        actor_id=actor_id,
        upstream_task_id=task_id,
        handle="lifecycle-hitl-off",
    )
    _claim_task_in_progress(session_factory, actor_id=actor_id, task_id=task_id)
    app = create_app(session_factory=session_factory, artifact_root=tmp_path)

    with TestClient(app) as client:
        packet_response = client.get(
            f"/v1/tasks/{task_id}/packet",
            headers=_headers(token),
        )
        submit_response = client.post(
            f"/v1/tasks/{task_id}/submit",
            headers=_post_headers(token),
            json=_valid_submission(),
        )
        submit_body = submit_response.json()
        confirm_response = client.post(
            f"/v1/learnings/drafts/{submit_body['learning_drafts'][0]['id']}/confirm",
            headers=_post_headers(token),
        )

    assert packet_response.status_code == 200
    assert packet_response.json()["task"]["id"] == str(task_id)

    assert submit_response.status_code == 200
    assert submit_body["task"]["state"] == "done"
    assert submit_body["task"]["claimed_by_actor_id"] is None
    assert submit_body["run"]["status"] == "done"
    assert submit_body["next_action"] == "done"
    assert [item["state"] for item in submit_body["transitions"]] == [
        "submitted",
        "validated",
        "done",
    ]
    assert len(submit_body["artifacts"]) == 2
    assert len(submit_body["learning_drafts"]) == 1

    assert confirm_response.status_code == 200
    confirm_body = confirm_response.json()
    assert confirm_body["draft"]["draft_status"] == "confirmed"

    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        downstream = session.get(TaskRecord, downstream_task_id)
        unblocked_audits = session.scalars(
            sa.select(AuditLogRecord)
            .where(
                AuditLogRecord.entity_type == "task",
                AuditLogRecord.entity_id == downstream_task_id,
                AuditLogRecord.action == "JOB_UNBLOCKED",
            )
            .order_by(AuditLogRecord.created_at.asc(), AuditLogRecord.id.asc())
        ).all()

        assert task is not None
        assert task.state == "done"
        assert task.claimed_by_actor_id is None
        assert downstream is not None
        assert downstream.state == "queued"
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(RunRecord)
                .where(RunRecord.task_id == task_id)
            )
            == 1
        )
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(PacketVersionRecord)
                .where(PacketVersionRecord.task_id == task_id)
            )
            == 1
        )
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(ArtifactRecord)
                .where(ArtifactRecord.task_id == task_id)
            )
            == 2
        )
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(LearningRecord)
                .where(LearningRecord.task_id == task_id)
            )
            == 1
        )
        assert len(unblocked_audits) == 1
        assert unblocked_audits[0].after == {
            "state": "queued",
            "dependency_task_id": str(task_id),
            "dependency_count": 1,
        }


def test_hitl_on_waits_for_manual_approval_before_unblocking_next_task(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    _write_submission_artifacts(tmp_path)
    actor_id, project_id, task_id, _ = _seed_task_with_token(
        session_factory,
        handle="lifecycle-hitl-on",
        grant_query_graph=True,
        grant_capabilities=(
            CapabilityKey.RUN_TESTS,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
            CapabilityKey.WRITE_LEARNING,
        ),
        task_state="queued",
        contract=_example_contract(),
    )
    token = _issue_learning_token(session_factory, actor_id=actor_id)
    downstream_task_id = _create_blocked_dependent(
        session_factory,
        project_id=project_id,
        actor_id=actor_id,
        upstream_task_id=task_id,
        handle="lifecycle-hitl-on",
    )
    _claim_task_in_progress(session_factory, actor_id=actor_id, task_id=task_id)
    app = create_app(session_factory=session_factory, artifact_root=tmp_path)

    with TestClient(app) as client:
        packet_response = client.get(
            f"/v1/tasks/{task_id}/packet",
            headers=_headers(token),
        )
        submit_response = client.post(
            f"/v1/tasks/{task_id}/submit",
            headers=_post_headers(token),
            json=_valid_submission(),
        )
        with session_factory() as session:
            downstream_before_approval = session.get(TaskRecord, downstream_task_id)
            assert downstream_before_approval is not None
            assert downstream_before_approval.state == "blocked"
        approve_response = client.post(
            f"/v1/tasks/{task_id}/approve",
            headers=_post_headers(token),
            json={"reason": "review complete"},
        )

    assert packet_response.status_code == 200
    assert submit_response.status_code == 200
    submit_body = submit_response.json()
    assert submit_body["task"]["state"] == "validated"
    assert submit_body["next_action"] == "await_human_approval"
    assert [item["state"] for item in submit_body["transitions"]] == [
        "submitted",
        "validated",
    ]
    assert len(submit_body["learning_drafts"]) == 1

    assert approve_response.status_code == 200
    approve_body = approve_response.json()
    assert approve_body["state"] == "done"
    assert approve_body["claimed_by_actor_id"] is None

    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        downstream = session.get(TaskRecord, downstream_task_id)
        assert task is not None
        assert task.state == "done"
        assert downstream is not None
        assert downstream.state == "queued"


def test_retry_after_validator_rejection_keeps_lineage_intact_on_success(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    _write_submission_artifacts(tmp_path)
    actor_id, _, task_id, _ = _seed_task_with_token(
        session_factory,
        handle="lifecycle-retry",
        grant_query_graph=True,
        grant_capabilities=(
            CapabilityKey.RUN_TESTS,
            CapabilityKey.CREATE_ARTIFACT,
            CapabilityKey.UPDATE_TASK,
            CapabilityKey.WRITE_LEARNING,
        ),
        task_state="queued",
        contract=_example_contract(),
    )
    token = _issue_learning_token(session_factory, actor_id=actor_id)
    _attach_task_policy(
        session_factory,
        task_id,
        name="lifecycle-retry-policy",
        hitl_required=False,
    )
    _claim_task_in_progress(session_factory, actor_id=actor_id, task_id=task_id)
    app = create_app(session_factory=session_factory, artifact_root=tmp_path)

    invalid_payload = _valid_submission()
    invalid_payload["output"] = dict(invalid_payload["output"])
    invalid_payload["output"].pop("diff_url")

    retry_payload = _valid_submission()
    retry_payload["had_retry"] = True

    with TestClient(app) as client:
        packet_response = client.get(
            f"/v1/tasks/{task_id}/packet",
            headers=_headers(token),
        )
        rejected_response = client.post(
            f"/v1/tasks/{task_id}/submit",
            headers=_post_headers(token),
            json=invalid_payload,
        )
        with session_factory() as session:
            mid_retry_task = session.get(TaskRecord, task_id)
            assert mid_retry_task is not None
            assert mid_retry_task.state == "in_progress"
            assert mid_retry_task.claimed_by_actor_id == actor_id
            assert (
                session.scalar(
                    sa.select(sa.func.count())
                    .select_from(RunRecord)
                    .where(RunRecord.task_id == task_id)
                )
                == 0
            )
        success_response = client.post(
            f"/v1/tasks/{task_id}/submit",
            headers=_post_headers(token),
            json=retry_payload,
        )

    assert packet_response.status_code == 200
    assert rejected_response.status_code == 422
    assert rejected_response.json()["message"] == "Task submission failed validation"

    assert success_response.status_code == 200
    success_body = success_response.json()
    assert success_body["task"]["state"] == "done"
    assert success_body["task"]["claimed_by_actor_id"] is None
    assert success_body["run"]["status"] == "done"
    assert success_body["next_action"] == "done"
    assert len(success_body["artifacts"]) == 2
    assert len(success_body["learning_drafts"]) == 1

    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        submitted_audits = session.scalars(
            sa.select(AuditLogRecord)
            .where(
                AuditLogRecord.entity_type == "task",
                AuditLogRecord.entity_id == task_id,
                AuditLogRecord.action == "JOB_SUBMITTED",
            )
            .order_by(AuditLogRecord.created_at.asc(), AuditLogRecord.id.asc())
        ).all()

        assert task is not None
        assert task.state == "done"
        assert task.claimed_by_actor_id is None
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(RunRecord)
                .where(RunRecord.task_id == task_id)
            )
            == 1
        )
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(PacketVersionRecord)
                .where(PacketVersionRecord.task_id == task_id)
            )
            == 1
        )
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(ArtifactRecord)
                .where(ArtifactRecord.task_id == task_id)
            )
            == 2
        )
        assert len(submitted_audits) == 1
