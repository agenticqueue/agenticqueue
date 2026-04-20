"""Declarative task transition engine."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agenticqueue_api.capability_keys import CapabilityKey
from agenticqueue_api.dod import DodReport
from agenticqueue_api.dod_checks.common import DodItemState
from agenticqueue_api.models.task import TaskModel
from agenticqueue_api.policy import PolicyLoadError
from agenticqueue_api.task_type_registry import TaskTypeRegistry


class TaskState(StrEnum):
    """Supported task lifecycle states."""

    QUEUED = "queued"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    VALIDATED = "validated"
    DONE = "done"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    PARKED = "parked"
    NEEDS_GHOST_TRIAGE = "needs_ghost_triage"


class InvalidTransitionError(ValueError):
    """Raised when a requested transition is not allowed by policy."""

    def __init__(
        self,
        *,
        from_state: TaskState,
        to_state: TaskState,
        allowed_transitions: tuple[TaskState, ...],
    ) -> None:
        self.from_state = from_state.value
        self.to_state = to_state.value
        self.allowed_transitions = tuple(state.value for state in allowed_transitions)
        allowed_text = ", ".join(self.allowed_transitions) or "<none>"
        super().__init__(
            f"Invalid transition {self.from_state} -> {self.to_state}. "
            f"Allowed transitions: {allowed_text}."
        )


@dataclass(frozen=True)
class TransitionPolicy:
    """Parsed transition policy for one task type."""

    task_type: str
    version: str
    hitl_required: bool
    autonomy_tier: int
    capabilities: tuple[CapabilityKey, ...]
    transitions: dict[TaskState, tuple[TaskState, ...]]
    required_capabilities: dict[TaskState, tuple[CapabilityKey, ...]]
    max_retries: int
    blocked_hours: int
    blocked_escalation_target: TaskState


@dataclass(frozen=True)
class TransitionResult:
    """Outcome of evaluating one transition request."""

    from_state: str
    requested_state: str
    state: str
    attempt_count: int
    allowed_transitions: tuple[str, ...]
    guard_blocked: str | None = None
    note: str | None = None
    escalation: str | None = None


class _RetryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_retries: int = Field(default=3, ge=1)


class _EscalationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocked_hours: int = Field(default=24, ge=1)
    target_state: TaskState = TaskState.NEEDS_GHOST_TRIAGE


class _StateMachinePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transitions: dict[TaskState, tuple[TaskState, ...]]
    required_capabilities: dict[TaskState, tuple[CapabilityKey, ...]] = Field(
        default_factory=dict
    )
    retry: _RetryPayload = Field(default_factory=_RetryPayload)
    escalation: _EscalationPayload = Field(default_factory=_EscalationPayload)

    @field_validator("transitions")
    @classmethod
    def validate_transitions(
        cls,
        value: dict[TaskState, tuple[TaskState, ...]],
    ) -> dict[TaskState, tuple[TaskState, ...]]:
        if not value:
            raise ValueError("transitions must not be empty")
        return value


class _TransitionPolicyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    hitl_required: bool
    autonomy_tier: int = Field(ge=1, le=5)
    capabilities: tuple[CapabilityKey, ...] = Field(default_factory=tuple)
    body: _StateMachinePayload


def _first_validation_error(error: ValidationError) -> str:
    detail = error.errors(include_url=False)[0]
    location = ".".join(str(part) for part in detail["loc"])
    message = detail["msg"]
    return f"{location}: {message}" if location else message


def _normalize_capabilities(
    values: Iterable[CapabilityKey | str],
) -> set[CapabilityKey]:
    return {
        value if isinstance(value, CapabilityKey) else CapabilityKey(str(value))
        for value in values
    }


def _as_utc(timestamp: dt.datetime) -> dt.datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=dt.UTC)
    return timestamp.astimezone(dt.UTC)


def load_transition_policy(
    task_type: str,
    registry: TaskTypeRegistry,
) -> TransitionPolicy:
    """Load one task-type transition policy from the registry."""

    definition = registry.get(task_type)
    try:
        payload = _TransitionPolicyPayload.model_validate(definition.policy)
    except ValidationError as error:
        detail = _first_validation_error(error)
        raise PolicyLoadError(
            f"Invalid transition policy for {task_type}: {detail}"
        ) from error

    return TransitionPolicy(
        task_type=task_type,
        version=payload.version,
        hitl_required=payload.hitl_required,
        autonomy_tier=payload.autonomy_tier,
        capabilities=payload.capabilities,
        transitions=dict(payload.body.transitions),
        required_capabilities=dict(payload.body.required_capabilities),
        max_retries=payload.body.retry.max_retries,
        blocked_hours=payload.body.escalation.blocked_hours,
        blocked_escalation_target=payload.body.escalation.target_state,
    )


def apply_transition(
    task: TaskModel,
    target_state: TaskState | str,
    registry: TaskTypeRegistry,
    *,
    actor_capabilities: Iterable[CapabilityKey | str] = (),
    attempt_count: int = 0,
    dod_report: DodReport | None = None,
    blocked_at: dt.datetime | None = None,
    now: dt.datetime | None = None,
    human_approved: bool = False,
) -> TransitionResult:
    """Evaluate one requested state transition against task policy and guards."""

    policy = load_transition_policy(task.task_type, registry)
    from_state = TaskState(task.state)
    requested_state = (
        target_state if isinstance(target_state, TaskState) else TaskState(target_state)
    )
    allowed = policy.transitions.get(from_state, ())
    if requested_state not in allowed:
        raise InvalidTransitionError(
            from_state=from_state,
            to_state=requested_state,
            allowed_transitions=allowed,
        )

    capability_set = _normalize_capabilities(actor_capabilities)
    missing_capabilities = [
        capability.value
        for capability in policy.required_capabilities.get(requested_state, ())
        if capability not in capability_set
    ]
    if missing_capabilities:
        missing = ", ".join(missing_capabilities)
        return TransitionResult(
            from_state=from_state.value,
            requested_state=requested_state.value,
            state=from_state.value,
            attempt_count=attempt_count,
            allowed_transitions=tuple(state.value for state in allowed),
            guard_blocked="capability",
            note=f"Missing required capabilities: {missing}.",
        )

    task_autonomy_tier = int(task.contract.get("autonomy_tier", 1))
    if task_autonomy_tier > policy.autonomy_tier:
        return TransitionResult(
            from_state=from_state.value,
            requested_state=requested_state.value,
            state=from_state.value,
            attempt_count=attempt_count,
            allowed_transitions=tuple(state.value for state in allowed),
            guard_blocked="policy",
            note=(
                f"Task autonomy tier {task_autonomy_tier} exceeds policy limit "
                f"{policy.autonomy_tier}."
            ),
        )

    if requested_state is TaskState.DONE and policy.hitl_required and not human_approved:
        return TransitionResult(
            from_state=from_state.value,
            requested_state=requested_state.value,
            state=from_state.value,
            attempt_count=attempt_count,
            allowed_transitions=tuple(state.value for state in allowed),
            guard_blocked="policy",
            note="Transition to done requires human approval by policy.",
        )

    if requested_state is TaskState.DONE:
        first_failure = None
        if dod_report is not None:
            first_failure = next(
                (
                    item
                    for item in dod_report.checklist
                    if item.state != DodItemState.CHECKED
                ),
                None,
            )
        if dod_report is None or first_failure is not None:
            note = (
                "Transition to done requires a passing DodReport."
                if first_failure is None
                else f"{first_failure.item}: {first_failure.note}"
            )
            return TransitionResult(
                from_state=from_state.value,
                requested_state=requested_state.value,
                state=from_state.value,
                attempt_count=attempt_count,
                allowed_transitions=tuple(state.value for state in allowed),
                guard_blocked="dod",
                note=note,
            )

    if (
        from_state is TaskState.BLOCKED
        and requested_state is policy.blocked_escalation_target
    ):
        blocked_age = None
        if blocked_at is not None:
            reference_now = _as_utc(now or dt.datetime.now(dt.UTC))
            blocked_age = reference_now - _as_utc(blocked_at)
        threshold = dt.timedelta(hours=policy.blocked_hours)
        if blocked_age is None or blocked_age < threshold:
            return TransitionResult(
                from_state=from_state.value,
                requested_state=requested_state.value,
                state=from_state.value,
                attempt_count=attempt_count,
                allowed_transitions=tuple(state.value for state in allowed),
                guard_blocked="policy",
                note=(
                    "Blocked escalation requires the task to remain blocked for at "
                    f"least {policy.blocked_hours} hours."
                ),
            )

    next_attempt_count = attempt_count
    escalation = None
    if from_state is TaskState.REJECTED and requested_state is TaskState.QUEUED:
        next_attempt_count += 1
        if next_attempt_count >= policy.max_retries:
            escalation = "max_retries_exceeded"

    if (
        from_state is TaskState.BLOCKED
        and requested_state is policy.blocked_escalation_target
    ):
        escalation = "blocked_too_long"

    return TransitionResult(
        from_state=from_state.value,
        requested_state=requested_state.value,
        state=requested_state.value,
        attempt_count=next_attempt_count,
        allowed_transitions=tuple(state.value for state in allowed),
        escalation=escalation,
    )
