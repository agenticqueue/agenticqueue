"""Task submission pipeline and escrow-unlock helpers."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, replace
from pathlib import Path
import uuid
from typing import Any

import sqlalchemy as sa
from pydantic import Field
from sqlalchemy.orm import Session

from agenticqueue_api.audit import AUDIT_REDACTION_KEY, AUDIT_TRACE_ID_KEY
from agenticqueue_api.capabilities import ensure_actor_has_capability
from agenticqueue_api.capability_keys import CapabilityKey
from agenticqueue_api.compiler import compile_packet, resolve_task_policy
from agenticqueue_api.dod import DodChecklistResult, DodReport
from agenticqueue_api.dod_checks.common import DodItemState
from agenticqueue_api.errors import raise_api_error
from agenticqueue_api.learnings.draft import (
    DraftLearningRecord,
    DraftLearningStatus,
    DraftLearningView,
    DraftStore,
)
from agenticqueue_api.models import (
    ActorModel,
    ArtifactModel,
    ArtifactRecord,
    AuditLogRecord,
    RunModel,
    RunRecord,
    TaskModel,
    TaskRecord,
)
from agenticqueue_api.models.edge import EdgeRecord, EdgeRelation
from agenticqueue_api.models.edge import edge_metadata_marks_superseded
from agenticqueue_api.models.shared import SchemaModel
from agenticqueue_api.packet_versions import get_current_packet_version
from agenticqueue_api.repo import release_claim
from agenticqueue_api.schemas.submit import (
    SubmitArtifactModel,
    TaskCompletionSubmission,
    validate_task_completion_submission,
)
from agenticqueue_api.task_retry import (
    increment_attempt_metric,
    resolve_max_attempts,
    with_retry_fields,
)
from agenticqueue_api.task_type_registry import TaskTypeRegistry
from agenticqueue_api.transitions import (
    TaskState,
    TransitionPolicy,
    TransitionResult,
    apply_transition,
    load_transition_policy,
)
from agenticqueue_api.validator import SubmissionValidator


class DodChecklistResultView(SchemaModel):
    """Serializable DoD checklist item view."""

    item: str
    state: str
    note: str


class DodReportView(SchemaModel):
    """Serializable DoD report returned from the submit pipeline."""

    checklist: list[DodChecklistResultView] = Field(default_factory=list)
    checked_count: int
    partial_count: int
    unchecked_blocked_count: int
    unchecked_unmet_count: int


class TransitionResultView(SchemaModel):
    """Serializable transition-evaluation result."""

    from_state: str
    requested_state: str
    state: str
    attempt_count: int
    allowed_transitions: list[str] = Field(default_factory=list)
    guard_blocked: str | None = None
    note: str | None = None
    escalation: str | None = None


class SubmitTaskResponse(SchemaModel):
    """Response payload for one accepted task submission."""

    task: TaskModel
    run: RunModel
    artifacts: list[ArtifactModel] = Field(default_factory=list)
    learning_drafts: list[DraftLearningView] = Field(default_factory=list)
    dod_report: DodReportView | None = None
    transitions: list[TransitionResultView] = Field(default_factory=list)
    next_action: str


class EscrowUnlockRequest(SchemaModel):
    """Payload for a forced escrow unlock."""

    reason: str | None = None


class TaskDecisionRequest(SchemaModel):
    """Optional reason payload for approve/reject endpoints."""

    reason: str | None = None


def _audit(
    session: Session,
    *,
    actor_id: uuid.UUID | None,
    task_id: uuid.UUID,
    action: str,
    after: dict[str, Any],
) -> None:
    session.execute(
        sa.insert(AuditLogRecord).values(
            actor_id=actor_id,
            entity_type="task",
            entity_id=task_id,
            action=action,
            before=None,
            after=after,
            trace_id=session.info.get(AUDIT_TRACE_ID_KEY),
            redaction=session.info.get(AUDIT_REDACTION_KEY),
        )
    )


def _dod_report_view(task_validation: Any) -> DodReportView | None:
    dod_report = getattr(task_validation, "dod_report", None)
    if dod_report is None:
        return None
    return DodReportView(
        checklist=[
            DodChecklistResultView(
                item=result.item,
                state=result.state.value,
                note=result.note,
            )
            for result in dod_report.checklist
        ],
        checked_count=dod_report.checked_count,
        partial_count=dod_report.partial_count,
        unchecked_blocked_count=dod_report.unchecked_blocked_count,
        unchecked_unmet_count=dod_report.unchecked_unmet_count,
    )


def _transition_view(result: TransitionResult) -> TransitionResultView:
    return TransitionResultView(
        from_state=result.from_state,
        requested_state=result.requested_state,
        state=result.state,
        attempt_count=result.attempt_count,
        allowed_transitions=list(result.allowed_transitions),
        guard_blocked=result.guard_blocked,
        note=result.note,
        escalation=result.escalation,
    )


def _project_scope(task_record: TaskRecord) -> dict[str, str]:
    return {"project_id": str(task_record.project_id)}


def _require_update_task_capability(
    session: Session,
    *,
    actor: ActorModel,
    task_record: TaskRecord,
) -> None:
    ensure_actor_has_capability(
        session,
        actor=actor,
        capability=CapabilityKey.UPDATE_TASK,
        required_scope=_project_scope(task_record),
        entity_type="task",
        entity_id=task_record.id,
    )


def _transition_or_conflict(
    result: TransitionResult,
    *,
    default_message: str,
) -> TransitionResult:
    if result.guard_blocked is None:
        return result
    raise_api_error(
        409,
        result.note or default_message,
        details=result.__dict__,
    )


def _dod_report_from_payload(payload: dict[str, Any] | None) -> DodReport | None:
    if not isinstance(payload, dict):
        return None
    raw_checklist = payload.get("checklist")
    if not isinstance(raw_checklist, list) or not raw_checklist:
        return None

    checklist: list[DodChecklistResult] = []
    for item in raw_checklist:
        if not isinstance(item, dict):
            return None
        raw_item = item.get("item")
        raw_state = item.get("state")
        raw_note = item.get("note", "")
        if not isinstance(raw_item, str) or not raw_item.strip():
            return None
        if not isinstance(raw_state, str):
            return None
        try:
            state = DodItemState(raw_state)
        except ValueError:
            return None
        note = raw_note if isinstance(raw_note, str) else str(raw_note)
        checklist.append(
            DodChecklistResult(
                item=raw_item,
                state=state,
                note=note,
            )
        )

    return DodReport(
        checklist=tuple(checklist),
        checked_count=sum(item.state == DodItemState.CHECKED for item in checklist),
        partial_count=sum(item.state == DodItemState.PARTIAL for item in checklist),
        unchecked_blocked_count=sum(
            item.state == DodItemState.UNCHECKED_BLOCKED for item in checklist
        ),
        unchecked_unmet_count=sum(
            item.state == DodItemState.UNCHECKED_UNMET for item in checklist
        ),
    )


def _latest_dod_report(session: Session, *, task_id: uuid.UUID) -> DodReport | None:
    run_record = session.scalar(
        sa.select(RunRecord)
        .where(RunRecord.task_id == task_id)
        .order_by(RunRecord.created_at.desc(), RunRecord.id.desc())
        .limit(1)
    )
    if run_record is None or not isinstance(run_record.details, dict):
        return None
    raw_report = run_record.details.get("dod_report")
    return _dod_report_from_payload(
        raw_report if isinstance(raw_report, dict) else None
    )


def _effective_transition_policy(
    session: Session,
    *,
    task_record: TaskRecord,
    task_type_registry: TaskTypeRegistry,
) -> TransitionPolicy:
    base_policy = load_transition_policy(task_record.task_type, task_type_registry)
    resolved_policy = resolve_task_policy(
        session,
        task_record,
        task_type_registry=task_type_registry,
    )
    return replace(
        base_policy,
        hitl_required=resolved_policy.hitl_required,
        autonomy_tier=resolved_policy.autonomy_tier,
        capabilities=tuple(resolved_policy.capabilities),
        max_retries=resolve_max_attempts(
            task_type=task_record.task_type,
            policy_body=resolved_policy.body,
            default=base_policy.max_retries,
        ),
    )


def _failure_payload(
    *,
    error_code: str,
    message: str,
    details: dict[str, Any],
    occurred_at: dt.datetime,
) -> dict[str, Any]:
    return {
        "error_code": error_code,
        "message": message,
        "details": details,
        "occurred_at": occurred_at.isoformat(),
    }


def _record_task_failure(
    session: Session,
    *,
    task_record: TaskRecord,
    actor: ActorModel,
    max_attempts: int,
    error_code: str,
    message: str,
    details: dict[str, Any],
    task_type_registry: TaskTypeRegistry,
    transition_policy: TransitionPolicy | None = None,
) -> TaskModel:
    now = dt.datetime.now(dt.UTC)
    next_attempt_count = task_record.attempt_count + 1
    task_record.attempt_count = next_attempt_count
    task_record.last_failure = _failure_payload(
        error_code=error_code,
        message=message,
        details=details,
        occurred_at=now,
    )
    task_record.state = (
        TaskState.DLQ.value
        if next_attempt_count >= max_attempts
        else TaskState.QUEUED.value
    )
    task_record.claimed_by_actor_id = None
    task_record.claimed_at = None
    session.flush()
    session.refresh(task_record)

    remaining = max(max_attempts - next_attempt_count, 0)
    _audit(
        session,
        actor_id=actor.id,
        task_id=task_record.id,
        action="JOB_FAILED",
        after={
            "state": task_record.state,
            "attempt_count": next_attempt_count,
            "max_attempts": max_attempts,
            "remaining_attempts": remaining,
            "last_failure": task_record.last_failure,
        },
    )
    if task_record.state == TaskState.DLQ.value:
        _audit(
            session,
            actor_id=actor.id,
            task_id=task_record.id,
            action="JOB_DLQ_ENTERED",
            after={
                "state": task_record.state,
                "attempt_count": next_attempt_count,
                "max_attempts": max_attempts,
                "remaining_attempts": 0,
                "last_failure": task_record.last_failure,
            },
        )
        increment_attempt_metric("dlq")

    return with_retry_fields(
        session,
        task_record,
        task_type_registry=task_type_registry,
        transition_policy=transition_policy,
    )


def _submission_artifacts(
    submission: TaskCompletionSubmission,
) -> list[SubmitArtifactModel]:
    items = list(submission.output.artifacts)
    seen_uris = {item.uri for item in items}

    if submission.output.diff_url not in seen_uris:
        items.append(
            SubmitArtifactModel(
                kind="diff",
                uri=submission.output.diff_url,
                details={"source": "output.diff_url"},
            )
        )
        seen_uris.add(submission.output.diff_url)

    if submission.output.test_report not in seen_uris:
        items.append(
            SubmitArtifactModel(
                kind="test-report",
                uri=submission.output.test_report,
                details={"source": "output.test_report"},
            )
        )

    return items


def _persist_learning_drafts(
    session: Session,
    *,
    task_id: uuid.UUID,
    run_id: uuid.UUID,
    submission: TaskCompletionSubmission,
) -> list[DraftLearningView]:
    store = DraftStore(session)
    created: list[DraftLearningView] = []
    for learning in submission.output.learnings:
        record = DraftLearningRecord(
            task_id=task_id,
            run_id=run_id,
            payload=learning.model_dump(mode="json"),
            draft_status=DraftLearningStatus.PENDING.value,
        )
        session.add(record)
        session.flush()
        session.refresh(record)
        draft = store.get(record.id)
        if draft is not None:
            created.append(draft)
    return created


def _active_dependency_target_ids(
    session: Session,
    *,
    task_id: uuid.UUID,
) -> list[uuid.UUID]:
    edges = session.scalars(
        sa.select(EdgeRecord)
        .where(
            EdgeRecord.src_entity_type == "task",
            EdgeRecord.src_id == task_id,
            EdgeRecord.dst_entity_type == "task",
            EdgeRecord.relation == EdgeRelation.DEPENDS_ON,
        )
        .order_by(EdgeRecord.created_at.asc(), EdgeRecord.id.asc())
    ).all()
    return [
        edge.dst_id
        for edge in edges
        if not edge_metadata_marks_superseded(edge.edge_metadata)
    ]


def _unblock_ready_dependents(
    session: Session,
    *,
    completed_task_id: uuid.UUID,
    actor_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    dependent_ids = session.scalars(
        sa.select(EdgeRecord.src_id)
        .where(
            EdgeRecord.src_entity_type == "task",
            EdgeRecord.dst_entity_type == "task",
            EdgeRecord.dst_id == completed_task_id,
            EdgeRecord.relation == EdgeRelation.DEPENDS_ON,
        )
        .order_by(EdgeRecord.created_at.asc(), EdgeRecord.id.asc())
    ).all()

    unblocked: list[uuid.UUID] = []
    for dependent_id in list(dict.fromkeys(dependent_ids)):
        dependent = session.get(TaskRecord, dependent_id)
        if dependent is None or dependent.state != TaskState.BLOCKED.value:
            continue

        dependency_ids = _active_dependency_target_ids(session, task_id=dependent_id)
        if not dependency_ids:
            continue

        dependency_states = {
            record.id: record.state
            for record in session.scalars(
                sa.select(TaskRecord).where(TaskRecord.id.in_(dependency_ids))
            )
        }
        if any(
            dependency_states.get(dependency_id) != TaskState.DONE.value
            for dependency_id in dependency_ids
        ):
            continue

        dependent.state = TaskState.QUEUED.value
        dependent.claimed_by_actor_id = None
        dependent.claimed_at = None
        session.flush()
        _audit(
            session,
            actor_id=actor_id,
            task_id=dependent.id,
            action="JOB_UNBLOCKED",
            after={
                "state": dependent.state,
                "dependency_task_id": str(completed_task_id),
                "dependency_count": len(dependency_ids),
            },
        )
        unblocked.append(dependent.id)

    return unblocked


def submit_task(
    session: Session,
    *,
    task_id: uuid.UUID,
    actor: ActorModel,
    submission: dict[str, Any] | TaskCompletionSubmission,
    task_type_registry: TaskTypeRegistry,
    artifact_root: Path,
    packet_cache: Any = None,
) -> SubmitTaskResponse:
    """Validate and persist one task submission atomically."""

    task_record = session.get(TaskRecord, task_id)
    if task_record is None:
        raise_api_error(404, "Task not found")

    if task_record.state != TaskState.IN_PROGRESS.value:
        raise_api_error(
            409,
            "Task must be in progress before submission",
            details={"task_id": str(task_id), "state": task_record.state},
        )

    if actor.actor_type != "admin" and task_record.claimed_by_actor_id != actor.id:
        raise_api_error(
            409,
            "Only the claiming actor may submit this task",
            details={
                "task_id": str(task_id),
                "claimed_by_actor_id": (
                    None
                    if task_record.claimed_by_actor_id is None
                    else str(task_record.claimed_by_actor_id)
                ),
                "actor_id": str(actor.id),
            },
        )

    task_model = TaskModel.model_validate(task_record)
    project_scope = _project_scope(task_record)

    ensure_actor_has_capability(
        session,
        actor=actor,
        capability=CapabilityKey.RUN_TESTS,
        required_scope=project_scope,
        entity_type="task",
        entity_id=task_record.id,
    )
    ensure_actor_has_capability(
        session,
        actor=actor,
        capability=CapabilityKey.CREATE_ARTIFACT,
        required_scope=project_scope,
        entity_type="task",
        entity_id=task_record.id,
    )
    ensure_actor_has_capability(
        session,
        actor=actor,
        capability=CapabilityKey.UPDATE_TASK,
        required_scope=project_scope,
        entity_type="task",
        entity_id=task_record.id,
    )

    policy = _effective_transition_policy(
        session,
        task_record=task_record,
        task_type_registry=task_type_registry,
    )

    validator = SubmissionValidator(task_type_registry, artifact_root=artifact_root)
    validation = validator.validate_submission(
        task_model,
        submission,
    )
    if not validation.is_valid:
        failure_details = {
            "task_id": str(task_id),
            "state": task_record.state,
            "errors": [asdict(error) for error in validation.errors],
        }
        failed_task = _record_task_failure(
            session,
            task_record=task_record,
            actor=actor,
            max_attempts=policy.max_retries,
            error_code="validation_failed",
            message="Task submission failed validation",
            details=failure_details,
            task_type_registry=task_type_registry,
            transition_policy=policy,
        )
        # Persist retry accounting even though the endpoint surfaces a 422.
        session.commit()
        raise_api_error(
            422,
            "Task submission failed validation",
            details=failure_details
            | {
                "attempt_count": failed_task.attempt_count,
                "max_attempts": failed_task.max_attempts,
                "remaining_attempts": failed_task.remaining_attempts,
                "task_state": failed_task.state,
            },
        )

    normalized_submission = validate_task_completion_submission(submission)
    packet_version = get_current_packet_version(session, task_record.id)
    if packet_version is None:
        compile_packet(
            session,
            task_record.id,
            task_type_registry=task_type_registry,
            packet_cache=packet_cache,
        )
        packet_version = get_current_packet_version(session, task_record.id)

    submitted = apply_transition(
        task_model,
        TaskState.SUBMITTED,
        task_type_registry,
        actor_capabilities=[CapabilityKey.RUN_TESTS],
        policy=policy,
    )
    _transition_or_conflict(
        submitted,
        default_message="Task cannot enter submitted state",
    )

    submitted_task = task_model.model_copy(update={"state": submitted.state})
    validated = apply_transition(
        submitted_task,
        TaskState.VALIDATED,
        task_type_registry,
        actor_capabilities=[CapabilityKey.UPDATE_TASK],
        dod_report=validation.dod_report,
        policy=policy,
    )
    _transition_or_conflict(
        validated,
        default_message="Task cannot enter validated state",
    )

    now = dt.datetime.now(dt.UTC)
    transitions = [submitted, validated]
    task_state = validated.state
    run_summary = "Task submission accepted and validated."
    next_action = "await_human_approval"

    if not policy.hitl_required:
        approved = apply_transition(
            submitted_task.model_copy(update={"state": validated.state}),
            TaskState.DONE,
            task_type_registry,
            actor_capabilities=[CapabilityKey.UPDATE_TASK],
            dod_report=validation.dod_report,
            policy=policy,
        )
        _transition_or_conflict(
            approved,
            default_message="Task cannot auto-approve into done state",
        )
        transitions.append(approved)
        task_state = approved.state
        run_summary = "Task submission accepted, validated, and auto-approved."
        next_action = "done"

    dod_report_view = _dod_report_view(validation)

    run_record = RunRecord(
        task_id=task_record.id,
        packet_version_id=None if packet_version is None else packet_version.id,
        actor_id=actor.id,
        status=task_state,
        started_at=task_record.claimed_at or now,
        ended_at=now,
        summary=run_summary,
        details={
            "submission": normalized_submission.model_dump(mode="json"),
            "transitions": [result.__dict__ for result in transitions],
            "next_action": next_action,
            "packet_version_id": (
                None if packet_version is None else str(packet_version.id)
            ),
            "dod_report": (
                None
                if dod_report_view is None
                else dod_report_view.model_dump(mode="json")
            ),
            "attempts": [
                {
                    "status": result.state,
                    "from_state": result.from_state,
                    "to_state": result.state,
                }
                for result in transitions
            ],
        },
    )
    session.add(run_record)
    session.flush()
    session.refresh(run_record)

    artifacts: list[ArtifactModel] = []
    for artifact in _submission_artifacts(normalized_submission):
        artifact_record = ArtifactRecord(
            task_id=task_record.id,
            run_id=run_record.id,
            kind=artifact.kind,
            uri=artifact.uri,
            details=artifact.details,
            embedding=None,
        )
        session.add(artifact_record)
        session.flush()
        session.refresh(artifact_record)
        artifacts.append(ArtifactModel.model_validate(artifact_record))
        session.add(
            EdgeRecord(
                src_entity_type="task",
                src_id=task_record.id,
                dst_entity_type="artifact",
                dst_id=artifact_record.id,
                relation=EdgeRelation.PRODUCED,
                edge_metadata={},
                created_by=actor.id,
            )
        )

    task_record.state = task_state
    task_record.claimed_by_actor_id = None
    task_record.claimed_at = None
    session.flush()
    session.refresh(task_record)

    learning_drafts = _persist_learning_drafts(
        session,
        task_id=task_record.id,
        run_id=run_record.id,
        submission=normalized_submission,
    )
    if task_record.state == TaskState.DONE.value:
        _unblock_ready_dependents(
            session,
            completed_task_id=task_record.id,
            actor_id=actor.id,
        )

    _audit(
        session,
        actor_id=actor.id,
        task_id=task_record.id,
        action="JOB_SUBMITTED",
        after={
            "run_id": str(run_record.id),
            "task_state": task_record.state,
            "artifact_count": len(artifacts),
            "learning_draft_count": len(learning_drafts),
            "next_action": next_action,
        },
    )

    if not policy.hitl_required:
        _audit(
            session,
            actor_id=actor.id,
            task_id=task_record.id,
            action="JOB_APPROVED",
            after={"state": task_record.state, "mode": "automatic"},
        )

    return SubmitTaskResponse(
        task=with_retry_fields(
            session,
            task_record,
            task_type_registry=task_type_registry,
            transition_policy=policy,
        ),
        run=RunModel.model_validate(run_record),
        artifacts=artifacts,
        learning_drafts=learning_drafts,
        dod_report=dod_report_view,
        transitions=[_transition_view(result) for result in transitions],
        next_action=next_action,
    )


def approve_task(
    session: Session,
    *,
    task_id: uuid.UUID,
    actor: ActorModel,
    task_type_registry: TaskTypeRegistry,
    reason: str | None = None,
) -> TaskModel:
    """Approve one validated task into the final done state."""

    task_record = session.get(TaskRecord, task_id)
    if task_record is None:
        raise_api_error(404, "Task not found")
    if task_record.state != TaskState.VALIDATED.value:
        raise_api_error(
            409,
            "Task must be awaiting approval before it can be approved",
            details={"task_id": str(task_id), "state": task_record.state},
        )

    _require_update_task_capability(session, actor=actor, task_record=task_record)
    dod_report = _latest_dod_report(session, task_id=task_record.id)
    if dod_report is None:
        raise_api_error(
            409,
            "Validated task is missing a persisted DoD report",
            details={"task_id": str(task_id)},
        )

    policy = _effective_transition_policy(
        session,
        task_record=task_record,
        task_type_registry=task_type_registry,
    )

    approved = apply_transition(
        TaskModel.model_validate(task_record),
        TaskState.DONE,
        task_type_registry,
        actor_capabilities=[CapabilityKey.UPDATE_TASK],
        dod_report=dod_report,
        human_approved=True,
        policy=policy,
    )
    _transition_or_conflict(
        approved,
        default_message="Task cannot be approved in its current state",
    )

    task_record.state = approved.state
    task_record.claimed_by_actor_id = None
    task_record.claimed_at = None
    session.flush()
    session.refresh(task_record)
    _unblock_ready_dependents(
        session,
        completed_task_id=task_record.id,
        actor_id=actor.id,
    )

    _audit(
        session,
        actor_id=actor.id,
        task_id=task_record.id,
        action="JOB_APPROVED",
        after={"state": task_record.state, "mode": "human", "reason": reason},
    )
    return with_retry_fields(
        session,
        task_record,
        task_type_registry=task_type_registry,
        transition_policy=policy,
    )


def reject_task(
    session: Session,
    *,
    task_id: uuid.UUID,
    actor: ActorModel,
    task_type_registry: TaskTypeRegistry,
    reason: str | None = None,
) -> TaskModel:
    """Reject one validated task and return it to the queue."""

    task_record = session.get(TaskRecord, task_id)
    if task_record is None:
        raise_api_error(404, "Task not found")
    if task_record.state != TaskState.VALIDATED.value:
        raise_api_error(
            409,
            "Task must be awaiting approval before it can be rejected",
            details={"task_id": str(task_id), "state": task_record.state},
        )

    _require_update_task_capability(session, actor=actor, task_record=task_record)
    policy = _effective_transition_policy(
        session,
        task_record=task_record,
        task_type_registry=task_type_registry,
    )

    rejected = apply_transition(
        TaskModel.model_validate(task_record),
        TaskState.REJECTED,
        task_type_registry,
        actor_capabilities=[CapabilityKey.UPDATE_TASK],
        policy=policy,
    )
    _transition_or_conflict(
        rejected,
        default_message="Task cannot be rejected in its current state",
    )

    requeued = apply_transition(
        TaskModel.model_validate(task_record).model_copy(
            update={"state": rejected.state}
        ),
        TaskState.QUEUED,
        task_type_registry,
        actor_capabilities=[CapabilityKey.UPDATE_TASK],
        attempt_count=task_record.attempt_count,
        policy=policy,
    )
    _transition_or_conflict(
        requeued,
        default_message="Rejected task cannot return to the queue",
    )

    task_record.attempt_count = requeued.attempt_count
    task_record.last_failure = _failure_payload(
        error_code="rejected",
        message="Task was rejected during review",
        details={"reason": reason},
        occurred_at=dt.datetime.now(dt.UTC),
    )
    task_record.state = (
        TaskState.DLQ.value
        if requeued.escalation == "max_retries_exceeded"
        else requeued.state
    )
    task_record.claimed_by_actor_id = None
    task_record.claimed_at = None
    session.flush()
    session.refresh(task_record)

    _audit(
        session,
        actor_id=actor.id,
        task_id=task_record.id,
        action="JOB_REJECTED",
        after={
            "state": task_record.state,
            "intermediate_state": rejected.state,
            "attempt_count": requeued.attempt_count,
            "reason": reason,
        },
    )
    if task_record.state == TaskState.DLQ.value:
        _audit(
            session,
            actor_id=actor.id,
            task_id=task_record.id,
            action="JOB_DLQ_ENTERED",
            after={
                "state": task_record.state,
                "attempt_count": task_record.attempt_count,
                "max_attempts": policy.max_retries,
                "remaining_attempts": 0,
                "last_failure": task_record.last_failure,
            },
        )
        increment_attempt_metric("dlq")
    return with_retry_fields(
        session,
        task_record,
        task_type_registry=task_type_registry,
        transition_policy=policy,
    )


def unlock_task_escrow(
    session: Session,
    *,
    task_id: uuid.UUID,
    actor: ActorModel,
    reason: str | None = None,
) -> TaskModel:
    """Force-release a claimed task back to a claimable state."""

    released = release_claim(session, task_id=task_id, expected_actor_id=None)
    if released is None:
        raise_api_error(404, "Task not found or not unlockable")

    _audit(
        session,
        actor_id=actor.id,
        task_id=task_id,
        action="ESCROW_FORCE_UNLOCKED",
        after={"state": released.state, "reason": reason},
    )
    task_record = session.get(TaskRecord, task_id)
    assert task_record is not None
    return with_retry_fields(session, task_record)
