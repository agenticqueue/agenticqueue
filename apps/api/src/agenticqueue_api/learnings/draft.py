"""Deterministic learning-draft generation for task closeout."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any

from agenticqueue_api.models.run import RunModel
from agenticqueue_api.models.task import TaskModel
from agenticqueue_api.schemas.learning import (
    LearningConfidence,
    LearningScope,
    LearningSchemaModel,
    LearningStatus,
    LearningType,
)
from agenticqueue_api.schemas.submit import (
    TaskCompletionSubmission,
    validate_task_completion_submission,
)

_DRAFT_OWNER = "agenticqueue-auto-draft"
_REVIEW_WINDOW_DAYS = 14
_MAX_EVIDENCE_ITEMS = 16


class DraftLearning(LearningSchemaModel):
    """One deterministic learning draft ready for review/edit."""


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


def _validator_rejection_attempts(details: Mapping[str, Any]) -> list[Mapping[str, Any]]:
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
            failed.extend(_string_or_none(item) for item in raw_tests)
        if any(marker in tokens for marker in ("test_failed", "pytest_failed", "failing test")):
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


__all__ = ["DraftLearning", "draft_learnings"]
