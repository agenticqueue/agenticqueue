"""Task submission pipeline and escrow-unlock helpers."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from pathlib import Path
import uuid
from typing import Any

import sqlalchemy as sa
from pydantic import Field
from sqlalchemy.orm import Session

from agenticqueue_api.audit import AUDIT_REDACTION_KEY, AUDIT_TRACE_ID_KEY
from agenticqueue_api.capabilities import ensure_actor_has_capability
from agenticqueue_api.capability_keys import CapabilityKey
from agenticqueue_api.compiler import compile_packet
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
from agenticqueue_api.models.shared import SchemaModel
from agenticqueue_api.packet_versions import get_current_packet_version
from agenticqueue_api.repo import release_claim
from agenticqueue_api.schemas.submit import (
    SubmitArtifactModel,
    TaskCompletionSubmission,
    validate_task_completion_submission,
)
from agenticqueue_api.task_type_registry import TaskTypeRegistry
from agenticqueue_api.transitions import (
    TaskState,
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
    project_scope = {"project_id": str(task_record.project_id)}

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

    validator = SubmissionValidator(task_type_registry, artifact_root=artifact_root)
    validation = validator.validate_submission(
        task_model,
        submission,
    )
    if not validation.is_valid:
        raise_api_error(
            422,
            "Task submission failed validation",
            details={
                "task_id": str(task_id),
                "state": task_record.state,
                "errors": [asdict(error) for error in validation.errors],
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
    )
    if submitted.guard_blocked is not None:
        raise_api_error(
            409,
            submitted.note or "Task cannot enter submitted state",
            details=submitted.__dict__,
        )

    submitted_task = task_model.model_copy(update={"state": submitted.state})
    validated = apply_transition(
        submitted_task,
        TaskState.VALIDATED,
        task_type_registry,
        actor_capabilities=[CapabilityKey.UPDATE_TASK],
        dod_report=validation.dod_report,
    )
    if validated.guard_blocked is not None:
        raise_api_error(
            409,
            validated.note or "Task cannot enter validated state",
            details=validated.__dict__,
        )

    now = dt.datetime.now(dt.UTC)
    policy = load_transition_policy(task_record.task_type, task_type_registry)
    next_action = (
        "await_human_approval" if policy.hitl_required else "ready_for_done"
    )
    transitions = [submitted, validated]

    run_record = RunRecord(
        task_id=task_record.id,
        packet_version_id=None if packet_version is None else packet_version.id,
        actor_id=actor.id,
        status=validated.state,
        started_at=task_record.claimed_at or now,
        ended_at=now,
        summary="Task submission accepted and validated.",
        details={
            "submission": normalized_submission.model_dump(mode="json"),
            "transitions": [result.__dict__ for result in transitions],
            "next_action": next_action,
            "packet_version_id": (
                None if packet_version is None else str(packet_version.id)
            ),
            "dod_report": (
                None
                if validation.dod_report is None
                else _dod_report_view(validation).model_dump(mode="json")
            ),
            "attempts": [
                {
                    "status": "submitted",
                    "from_state": submitted.from_state,
                    "to_state": submitted.state,
                },
                {
                    "status": "validated",
                    "from_state": validated.from_state,
                    "to_state": validated.state,
                },
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

    task_record.state = validated.state
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

    return SubmitTaskResponse(
        task=TaskModel.model_validate(task_record),
        run=RunModel.model_validate(run_record),
        artifacts=artifacts,
        learning_drafts=learning_drafts,
        dod_report=_dod_report_view(validation),
        transitions=[_transition_view(result) for result in transitions],
        next_action=next_action,
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
    return released
