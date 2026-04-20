"""Deterministic learning drafts plus the persisted draft lifecycle."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any
import uuid

from pydantic import Field, model_validator
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from agenticqueue_api.db import Base
from agenticqueue_api.learnings.dedupe import (
    ConfirmLearningDraftRequest,
    DedupeSuggestion,
    LearningDedupeService,
)
from agenticqueue_api.models.learning import LearningModel, LearningRecord
from agenticqueue_api.models.run import RunModel
from agenticqueue_api.models.shared import (
    IdentifiedTable,
    SchemaModel,
    TimestampedSchema,
    TimestampedTable,
    jsonb_dict_column,
)
from agenticqueue_api.models.task import TaskModel
from agenticqueue_api.schemas.learning import (
    DateText,
    LearningConfidence,
    LearningSchemaModel,
    LearningScope,
    LearningStatus,
    LearningType,
    LongText,
    MediumText,
    ShortText,
)
from agenticqueue_api.schemas.submit import (
    TaskCompletionSubmission,
    validate_task_completion_submission,
)

_DRAFT_OWNER = "agenticqueue-auto-draft"
_REVIEW_WINDOW_DAYS = 14
_MAX_EVIDENCE_ITEMS = 16


class DraftLearningStatus(StrEnum):
    """Lifecycle states for a persisted learning draft."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class DraftLearning(LearningSchemaModel):
    """One deterministic learning draft ready for review/edit."""


class DraftLearningPatch(SchemaModel):
    """Mutable fields on a persisted learning draft."""

    title: MediumText | None = None
    type: LearningType | None = None
    what_happened: LongText | None = None
    what_learned: LongText | None = None
    action_rule: LongText | None = None
    applies_when: MediumText | None = None
    does_not_apply_when: MediumText | None = None
    evidence: list[str] | None = Field(default=None, min_length=1, max_length=16)
    scope: LearningScope | None = None
    confidence: LearningConfidence | None = None
    status: LearningStatus | None = None
    owner: ShortText | None = None
    review_date: DateText | None = None

    @model_validator(mode="after")
    def validate_any_field_present(self) -> DraftLearningPatch:
        if not self.model_fields_set:
            raise ValueError("At least one draft field must be provided")
        return self


class DraftRejectRequest(SchemaModel):
    """Reject payload for a persisted learning draft."""

    reason: MediumText


class DraftLearningView(TimestampedSchema):
    """Persisted learning draft surfaced over the API."""

    task_id: uuid.UUID
    run_id: uuid.UUID
    draft_status: DraftLearningStatus
    rejection_reason: str | None = None
    confirmed_learning_id: uuid.UUID | None = None
    draft: DraftLearning


class ConfirmedDraftLearningView(SchemaModel):
    """Draft confirmation response with the promoted learning."""

    draft: DraftLearningView
    learning: LearningModel


class DraftLearningRecord(IdentifiedTable, TimestampedTable, Base):
    """SQLAlchemy row for one persisted learning draft."""

    __tablename__ = "learning_drafts"
    __table_args__ = (
        sa.Index("ix_learning_drafts_task_id", "task_id"),
        sa.Index("ix_learning_drafts_run_id", "run_id"),
        sa.Index("ix_learning_drafts_draft_status", "draft_status"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.task.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.run.id", ondelete="CASCADE"),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = jsonb_dict_column()
    draft_status: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        default=DraftLearningStatus.PENDING.value,
        server_default=sa.text("'pending'"),
    )
    rejection_reason: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    confirmed_learning_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("agenticqueue.learning.id", ondelete="SET NULL"),
        nullable=True,
    )


class DraftStore:
    """Persist and transition learning drafts."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_drafts(
        self,
        *,
        task: TaskModel,
        run: RunModel,
        submission: Mapping[str, Any] | TaskCompletionSubmission,
    ) -> list[DraftLearningView]:
        drafts = draft_learnings(task, run, submission)
        created: list[DraftLearningView] = []
        for draft in drafts:
            record = DraftLearningRecord(
                task_id=task.id,
                run_id=run.id,
                payload=draft.model_dump(mode="json"),
                draft_status=DraftLearningStatus.PENDING.value,
            )
            self._session.add(record)
            self._session.flush()
            self._session.refresh(record)
            created.append(self._view(record))
        return created

    def get(self, draft_id: uuid.UUID) -> DraftLearningView | None:
        record = self._session.get(DraftLearningRecord, draft_id)
        if record is None:
            return None
        return self._view(record)

    def edit(
        self,
        draft_id: uuid.UUID,
        patch: DraftLearningPatch,
    ) -> DraftLearningView:
        record = self._require_pending(draft_id)
        updated_payload = self._payload(record).model_dump(mode="json")
        updated_payload.update(patch.model_dump(mode="json", exclude_none=True))
        record.payload = DraftLearning.model_validate(updated_payload).model_dump(
            mode="json"
        )
        self._session.flush()
        self._session.refresh(record)
        return self._view(record)

    def reject(
        self,
        draft_id: uuid.UUID,
        *,
        reason: str,
    ) -> DraftLearningView:
        record = self._require_pending(draft_id)
        record.draft_status = DraftLearningStatus.REJECTED.value
        record.rejection_reason = reason
        self._session.flush()
        self._session.refresh(record)
        return self._view(record)

    def confirm(
        self,
        draft_id: uuid.UUID,
        *,
        owner_actor_id: uuid.UUID | None,
        request: ConfirmLearningDraftRequest | None = None,
        embed_text: Any = None,
    ) -> ConfirmedDraftLearningView | DedupeSuggestion:
        record = self._require_pending(draft_id)
        draft = LearningSchemaModel.model_validate(record.payload)
        dedupe = LearningDedupeService(self._session, embed_text=embed_text)
        suggestion = dedupe.suggest(draft_id=draft_id, draft_learning=draft)
        if suggestion is not None:
            if request is None or request.merge_decision is None:
                return suggestion
            if request.matched_learning_id != suggestion.matched_learning.id:
                raise ValueError(
                    "Dedupe suggestion is stale; retry confirmation to refresh it.",
                )
            if request.merge_decision.value == "accept":
                learning = dedupe.merge_into_existing(
                    existing_learning_id=suggestion.matched_learning.id,
                    draft_learning=draft,
                )
                self._mark_confirmed(record, learning_id=learning.id)
                return ConfirmedDraftLearningView(
                    draft=self._view(record),
                    learning=learning,
                )
        elif request is not None and request.merge_decision is not None:
            raise ValueError("No dedupe suggestion is available for this draft.")

        learning_record = self._create_learning_record(
            record=record,
            draft=draft,
            owner_actor_id=owner_actor_id,
            embed_text=embed_text,
        )
        if suggestion is not None:
            dedupe.create_related_edge(
                learning_id=learning_record.id,
                related_learning_id=suggestion.matched_learning.id,
                created_by=owner_actor_id,
            )

        self._mark_confirmed(record, learning_id=learning_record.id)
        return ConfirmedDraftLearningView(
            draft=self._view(record),
            learning=LearningModel.model_validate(learning_record),
        )

    def _create_learning_record(
        self,
        *,
        record: DraftLearningRecord,
        draft: LearningSchemaModel,
        owner_actor_id: uuid.UUID | None,
        embed_text: Any,
    ) -> LearningRecord:
        dedupe = LearningDedupeService(self._session, embed_text=embed_text)
        learning_record = LearningRecord(
            task_id=record.task_id,
            owner_actor_id=owner_actor_id,
            owner=draft.owner,
            title=draft.title,
            learning_type=draft.type.value,
            what_happened=draft.what_happened,
            what_learned=draft.what_learned,
            action_rule=draft.action_rule,
            applies_when=draft.applies_when,
            does_not_apply_when=draft.does_not_apply_when,
            evidence=draft.evidence,
            scope=draft.scope.value,
            confidence=draft.confidence.value,
            status=LearningStatus.ACTIVE.value,
            review_date=dt.date.fromisoformat(draft.review_date),
            embedding=dedupe.embed_learning_text(draft.title, draft.action_rule),
        )
        self._session.add(learning_record)
        self._session.flush()
        self._session.refresh(learning_record)
        return learning_record

    def _mark_confirmed(
        self,
        record: DraftLearningRecord,
        *,
        learning_id: uuid.UUID,
    ) -> None:
        record.draft_status = DraftLearningStatus.CONFIRMED.value
        record.confirmed_learning_id = learning_id
        record.rejection_reason = None
        self._session.flush()
        self._session.refresh(record)

    def _require_pending(self, draft_id: uuid.UUID) -> DraftLearningRecord:
        record = self._session.get(DraftLearningRecord, draft_id)
        if record is None:
            raise KeyError(str(draft_id))
        if record.draft_status != DraftLearningStatus.PENDING.value:
            raise ValueError(
                f"Learning draft {draft_id} is not pending; current status is "
                f"{record.draft_status}."
            )
        return record

    def _payload(self, record: DraftLearningRecord) -> DraftLearning:
        return DraftLearning.model_validate(record.payload)

    def _view(self, record: DraftLearningRecord) -> DraftLearningView:
        return DraftLearningView(
            id=record.id,
            created_at=record.created_at,
            updated_at=record.updated_at,
            task_id=record.task_id,
            run_id=record.run_id,
            draft_status=DraftLearningStatus(record.draft_status),
            rejection_reason=record.rejection_reason,
            confirmed_learning_id=record.confirmed_learning_id,
            draft=self._payload(record),
        )


def draft_learnings(
    task: TaskModel,
    run: RunModel,
    submission: Mapping[str, Any] | TaskCompletionSubmission,
) -> list[DraftLearning]:
    """Build deterministic learning drafts from one task/run/submission tuple."""

    normalized_submission = _normalize_submission(submission)
    details = _details_mapping(run)
    review_date = _review_date_for(run)
    drafts: list[DraftLearning] = []

    validator_attempts = _validator_rejection_attempts(details)
    if validator_attempts:
        failure_modes = _validator_failure_modes(validator_attempts)
        summary = (
            ", ".join(failure_modes[:2])
            if failure_modes
            else "submission contract requirements"
        )
        drafts.append(
            _build_draft(
                run=run,
                submission=normalized_submission,
                title=f"Repeated validator rejection on {summary}",
                learning_type=LearningType.PITFALL,
                what_happened=(
                    f'Task "{task.title}" hit {len(validator_attempts)} validator '
                    f"rejection(s) before the submission succeeded. The repeated "
                    f"failure mode was {summary}."
                ),
                what_learned=(
                    "Repeated validator rejections usually mean the expected output "
                    "shape was not grounded early enough in the run."
                ),
                action_rule=(
                    f"When validator feedback points at {summary}, update the "
                    "submission payload and rerun validation before spending "
                    "another full execution attempt."
                ),
                applies_when="A run is retried after validator or schema feedback.",
                does_not_apply_when=(
                    "The first submission passes validation without any contract edits."
                ),
                review_date=review_date,
                extra_evidence=[f"validator://{mode}" for mode in failure_modes],
            )
        )

    failed_tests = _failed_test_names(details)
    if failed_tests:
        summary = ", ".join(failed_tests[:2])
        drafts.append(
            _build_draft(
                run=run,
                submission=normalized_submission,
                title=f"Stabilize failing test path: {summary}",
                learning_type=LearningType.TOOLING,
                what_happened=(
                    f'Task "{task.title}" recorded test failure signals before the '
                    f"successful submission. The failing path included {summary}."
                ),
                what_learned=(
                    "Test-failure retries are a tooling feedback loop, so the next "
                    "attempt should start from the failing test names and runner "
                    "output instead of a broad code rework."
                ),
                action_rule=(
                    "Capture the failing test names and rerun the narrowest test "
                    "target before attempting another full submission."
                ),
                applies_when="The run trace includes failing tests or test-runner errors.",
                does_not_apply_when=(
                    "Validation failed before tests ran, or the run never reached the test step."
                ),
                review_date=review_date,
                extra_evidence=[f"test://{name}" for name in failed_tests],
            )
        )

    retry_count = _retry_count(details, normalized_submission)
    if retry_count > 0:
        drafts.append(
            _build_draft(
                run=run,
                submission=normalized_submission,
                title=f"Capture the recovery path after {retry_count} retry attempt(s)",
                learning_type=LearningType.PROCESS_RULE,
                what_happened=(
                    f'Task "{task.title}" required {retry_count} retry attempt(s) '
                    "before the run completed."
                ),
                what_learned=(
                    "A successful retry path is reusable process knowledge and should "
                    "be written down while the recovery sequence is still fresh."
                ),
                action_rule=(
                    "When a run needs retries, record the smallest sequence of checks "
                    "that moved it from failing to passing so the next actor can start there."
                ),
                applies_when="The run trace shows one or more retries before success.",
                does_not_apply_when="The task completes on the first attempt.",
                review_date=review_date,
                extra_evidence=[f"retry-count://{retry_count}"],
            )
        )

    if _was_blocked_then_resolved(details):
        drafts.append(
            _build_draft(
                run=run,
                submission=normalized_submission,
                title="Record the unblock step for recurring dependency stalls",
                learning_type=LearningType.DECISION_FOLLOWUP,
                what_happened=(
                    f'Task "{task.title}" was blocked and later resumed within the '
                    "same run trace."
                ),
                what_learned=(
                    "The unblock decision is durable context, because the next task "
                    "can often avoid the same stall by applying the same check earlier."
                ),
                action_rule=(
                    "When a blocked run becomes unblocked, capture the exact gate "
                    "that cleared and the evidence that proved the dependency was ready."
                ),
                applies_when="The run trace contains both blocked and resumed events.",
                does_not_apply_when=(
                    "The task never entered a blocked state during execution."
                ),
                review_date=review_date,
                extra_evidence=["run-event://blocked", "run-event://resolved"],
            )
        )

    reviewer_notes = _reviewer_correction_notes(details)
    if reviewer_notes:
        summary = reviewer_notes[0]
        drafts.append(
            _build_draft(
                run=run,
                submission=normalized_submission,
                title="Capture reviewer correction before the next handoff",
                learning_type=LearningType.USER_PREFERENCE,
                what_happened=(
                    f'Task "{task.title}" changed course after reviewer feedback. '
                    f"Representative correction: {summary}."
                ),
                what_learned=(
                    "Reviewer corrections are usually preference or style signals that "
                    "should be visible before the next actor repeats the same mismatch."
                ),
                action_rule=(
                    "When reviewer feedback changes the implementation, preserve the "
                    "correction in the draft so future packets inherit it."
                ),
                applies_when="A reviewer correction or feedback event changes the work.",
                does_not_apply_when=(
                    "No human or automated reviewer asked for a behavioral change."
                ),
                review_date=review_date,
                extra_evidence=[f"reviewer://{note}" for note in reviewer_notes],
            )
        )

    return drafts


def _build_draft(
    *,
    run: RunModel,
    submission: TaskCompletionSubmission,
    title: str,
    learning_type: LearningType,
    what_happened: str,
    what_learned: str,
    action_rule: str,
    applies_when: str,
    does_not_apply_when: str,
    review_date: str,
    extra_evidence: Sequence[str],
) -> DraftLearning:
    evidence = _evidence(run, submission, extra_evidence)
    return DraftLearning(
        title=title,
        type=learning_type,
        what_happened=what_happened,
        what_learned=what_learned,
        action_rule=action_rule,
        applies_when=applies_when,
        does_not_apply_when=does_not_apply_when,
        evidence=evidence,
        scope=LearningScope.TASK,
        confidence=LearningConfidence.TENTATIVE,
        status=LearningStatus.ACTIVE,
        owner=_DRAFT_OWNER,
        review_date=review_date,
    )


def _normalize_submission(
    submission: Mapping[str, Any] | TaskCompletionSubmission,
) -> TaskCompletionSubmission:
    if isinstance(submission, TaskCompletionSubmission):
        return submission
    return validate_task_completion_submission(submission)


def _details_mapping(run: RunModel) -> Mapping[str, Any]:
    return run.details if isinstance(run.details, Mapping) else {}


def _attempts(details: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = details.get("attempts", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _events(details: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = details.get("events", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _reviewer_correction_notes(details: Mapping[str, Any]) -> list[str]:
    notes: list[str] = []
    for key in ("reviewer_corrections", "reviewer_feedback"):
        raw = details.get(key, [])
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
            continue
        for item in raw:
            note = _string_or_none(item)
            if note:
                notes.append(note)
            elif isinstance(item, Mapping):
                for field in ("summary", "note", "message", "feedback"):
                    mapped = _string_or_none(item.get(field))
                    if mapped:
                        notes.append(mapped)
                        break

    for event in _events(details):
        if "reviewer" not in _event_kind(event):
            continue
        note = _string_or_none(
            event.get("summary") or event.get("message") or event.get("note")
        )
        if note:
            notes.append(note)
    return _dedupe(notes)


def _validator_rejection_attempts(
    details: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    matches: list[Mapping[str, Any]] = []
    for attempt in _attempts(details):
        tokens = _token_blob(attempt)
        if _validator_errors(attempt):
            matches.append(attempt)
            continue
        if any(
            marker in tokens
            for marker in (
                "validator_rejection",
                "validation_failed",
                "schema_failed",
                "rejected_by_validator",
            )
        ):
            matches.append(attempt)
            continue
        if "rejected" in tokens and any(
            marker in tokens for marker in ("validator", "schema")
        ):
            matches.append(attempt)
    return matches


def _validator_failure_modes(attempts: Sequence[Mapping[str, Any]]) -> list[str]:
    failure_modes: list[str] = []
    for attempt in attempts:
        for error in _validator_errors(attempt):
            if isinstance(error, Mapping):
                field = _string_or_none(
                    error.get("field")
                    or error.get("path")
                    or error.get("offending_field")
                    or error.get("loc")
                )
                if field:
                    failure_modes.append(field)
                    continue
                message = _string_or_none(error.get("message") or error.get("hint"))
                if message:
                    failure_modes.append(message)
                    continue
            note = _string_or_none(error)
            if note:
                failure_modes.append(note)
    return _dedupe(failure_modes)


def _validator_errors(attempt: Mapping[str, Any]) -> list[Any]:
    raw = attempt.get("validator_errors") or attempt.get("errors") or []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    return list(raw)


def _failed_test_names(details: Mapping[str, Any]) -> list[str]:
    failed: list[str] = []
    for attempt in _attempts(details):
        tokens = _token_blob(attempt)
        raw_tests = attempt.get("failed_tests", [])
        if isinstance(raw_tests, Sequence) and not isinstance(
            raw_tests, (str, bytes, bytearray)
        ):
            failed.extend(
                item_text
                for item in raw_tests
                if (item_text := _string_or_none(item)) is not None
            )
        if any(
            marker in tokens
            for marker in ("test_failed", "pytest_failed", "failing test")
        ):
            label = _string_or_none(
                attempt.get("summary") or attempt.get("reason") or attempt.get("status")
            )
            if label:
                failed.append(label)
    return _dedupe([item for item in failed if item])


def _retry_count(
    details: Mapping[str, Any],
    submission: TaskCompletionSubmission,
) -> int:
    raw = details.get("retry_count")
    if isinstance(raw, bool):
        raw = int(raw)
    if isinstance(raw, int):
        return max(raw, 0)
    attempts = _attempts(details)
    if len(attempts) > 1:
        return len(attempts) - 1
    return 1 if submission.had_retry else 0


def _was_blocked_then_resolved(details: Mapping[str, Any]) -> bool:
    event_kinds = [_event_kind(event) for event in _events(details)]
    has_block = any("block" in kind for kind in event_kinds)
    has_resolve = any(
        marker in kind
        for kind in event_kinds
        for marker in ("unblocked", "resumed", "resolved")
    )
    return has_block and has_resolve


def _event_kind(event: Mapping[str, Any]) -> str:
    for field in ("type", "status", "event", "name"):
        value = _string_or_none(event.get(field))
        if value:
            return value.lower()
    return ""


def _token_blob(mapping: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for field in ("status", "outcome", "result", "reason", "source", "error_source"):
        value = _string_or_none(mapping.get(field))
        if value:
            parts.append(value.lower())
    return " ".join(parts)


def _review_date_for(run: RunModel) -> str:
    reference = run.ended_at or run.updated_at or run.started_at
    return (reference.date() + dt.timedelta(days=_REVIEW_WINDOW_DAYS)).isoformat()


def _evidence(
    run: RunModel,
    submission: TaskCompletionSubmission,
    extra_evidence: Sequence[str],
) -> list[str]:
    evidence = [f"run://{run.id}", *extra_evidence]
    evidence.extend(artifact.uri for artifact in submission.output.artifacts)
    if submission.output.diff_url:
        evidence.append(submission.output.diff_url)
    if submission.output.test_report:
        evidence.append(submission.output.test_report)
    return _dedupe(evidence)[:_MAX_EVIDENCE_ITEMS]


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


__all__ = [
    "ConfirmLearningDraftRequest",
    "ConfirmedDraftLearningView",
    "DedupeSuggestion",
    "DraftLearning",
    "DraftLearningPatch",
    "DraftLearningRecord",
    "DraftLearningStatus",
    "DraftLearningView",
    "DraftRejectRequest",
    "DraftStore",
    "draft_learnings",
]
