from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import pytest

from agenticqueue_api.capability_keys import CapabilityKey
from agenticqueue_api.config import get_task_types_dir
from agenticqueue_api.dod import DodChecklistResult, DodReport
from agenticqueue_api.dod_checks.common import DodItemState
from agenticqueue_api.models.task import TaskModel
from agenticqueue_api.policy import PolicyLoadError
from agenticqueue_api.task_type_registry import TaskTypeRegistry
from agenticqueue_api.transitions import (
    InvalidTransitionError,
    TaskState,
    apply_transition,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _task_type_registry(directory: Path | None = None) -> TaskTypeRegistry:
    registry = TaskTypeRegistry(directory or get_task_types_dir())
    registry.load()
    return registry


def _make_task(
    *,
    state: str = "queued",
    autonomy_tier: int = 3,
) -> TaskModel:
    timestamp = "2026-04-20T00:00:00+00:00"
    return TaskModel.model_validate(
        {
            "id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "task_type": "coding-task",
            "title": "Transition test task",
            "state": state,
            "description": "Task payload used to validate the transition engine",
            "contract": {"autonomy_tier": autonomy_tier},
            "definition_of_done": ["DoD item 1"],
            "created_at": timestamp,
            "updated_at": timestamp,
        }
    )


def _dod_report(*states: DodItemState) -> DodReport:
    checklist = tuple(
        DodChecklistResult(
            item=f"DoD item {index}",
            state=state,
            note=f"Result {index}",
        )
        for index, state in enumerate(states, start=1)
    )
    return DodReport(
        checklist=checklist,
        checked_count=sum(item.state == DodItemState.CHECKED for item in checklist),
        partial_count=sum(item.state == DodItemState.PARTIAL for item in checklist),
        unchecked_blocked_count=sum(
            item.state == DodItemState.UNCHECKED_BLOCKED for item in checklist
        ),
        unchecked_unmet_count=sum(
            item.state == DodItemState.UNCHECKED_UNMET for item in checklist
        ),
    )


def test_valid_transition_moves_queued_task_to_claimed() -> None:
    result = apply_transition(
        _make_task(state="queued"),
        TaskState.CLAIMED,
        _task_type_registry(),
        actor_capabilities=["read_repo"],
    )

    assert result.state == "claimed"
    assert result.guard_blocked is None
    assert result.attempt_count == 0
    assert result.allowed_transitions == ("claimed", "parked")


def test_invalid_transition_raises_structured_error() -> None:
    with pytest.raises(InvalidTransitionError) as excinfo:
        apply_transition(_make_task(state="done"), "queued", _task_type_registry())

    with pytest.raises(InvalidTransitionError) as second_excinfo:
        apply_transition(_make_task(state="queued"), "done", _task_type_registry())

    error = excinfo.value
    assert error.from_state == "done"
    assert error.to_state == "queued"
    assert error.allowed_transitions == ()
    assert second_excinfo.value.allowed_transitions == ("claimed", "parked")


def test_retry_transition_increments_attempt_count_and_emits_escalation() -> None:
    below_threshold = apply_transition(
        _make_task(state="rejected"),
        "queued",
        _task_type_registry(),
        attempt_count=1,
    )
    result = apply_transition(
        _make_task(state="rejected"),
        "queued",
        _task_type_registry(),
        attempt_count=2,
    )

    assert below_threshold.state == "queued"
    assert below_threshold.attempt_count == 2
    assert below_threshold.escalation is None
    assert result.state == "queued"
    assert result.attempt_count == 3
    assert result.escalation == "max_retries_exceeded"


def test_capability_guard_blocks_transition_when_required_capability_is_missing() -> (
    None
):
    result = apply_transition(
        _make_task(state="claimed"),
        "in_progress",
        _task_type_registry(),
        actor_capabilities=[CapabilityKey.READ_REPO],
    )

    assert result.state == "claimed"
    assert result.guard_blocked == "capability"
    assert result.note == "Missing required capabilities: write_branch."


def test_policy_guards_block_autonomy_overreach_and_missing_human_approval() -> None:
    registry = _task_type_registry()

    autonomy_block = apply_transition(
        _make_task(state="claimed", autonomy_tier=4),
        "in_progress",
        registry,
        actor_capabilities=[CapabilityKey.WRITE_BRANCH],
    )
    approval_block = apply_transition(
        _make_task(state="validated"),
        "done",
        registry,
        actor_capabilities=[CapabilityKey.UPDATE_TASK],
        dod_report=_dod_report(DodItemState.CHECKED),
        human_approved=False,
    )

    assert autonomy_block.guard_blocked == "policy"
    assert autonomy_block.note == "Task autonomy tier 4 exceeds policy limit 3."
    assert approval_block.guard_blocked == "policy"
    assert (
        approval_block.note == "Transition to done requires human approval by policy."
    )


def test_dod_guard_blocks_done_transition_when_report_has_failure() -> None:
    result = apply_transition(
        _make_task(state="validated"),
        "done",
        _task_type_registry(),
        actor_capabilities=[CapabilityKey.UPDATE_TASK],
        dod_report=_dod_report(
            DodItemState.CHECKED,
            DodItemState.UNCHECKED_UNMET,
        ),
        human_approved=True,
    )

    assert result.state == "validated"
    assert result.guard_blocked == "dod"
    assert result.note == "DoD item 2: Result 2"


def test_done_transition_requires_a_passing_dod_report() -> None:
    result = apply_transition(
        _make_task(state="validated"),
        "done",
        _task_type_registry(),
        actor_capabilities=[CapabilityKey.UPDATE_TASK],
        human_approved=True,
    )

    assert result.state == "validated"
    assert result.guard_blocked == "dod"
    assert result.note == "Transition to done requires a passing DodReport."


def test_done_transition_succeeds_with_policy_approval_and_passing_dod_report() -> None:
    result = apply_transition(
        _make_task(state="validated"),
        "done",
        _task_type_registry(),
        actor_capabilities=[CapabilityKey.UPDATE_TASK],
        dod_report=_dod_report(DodItemState.CHECKED),
        human_approved=True,
    )

    assert result.state == "done"
    assert result.guard_blocked is None
    assert result.escalation is None


def test_blocked_escalation_requires_age_threshold_and_then_routes_to_ghost_triage() -> (
    None
):
    registry = _task_type_registry()
    now = dt.datetime(2026, 4, 20, 12, 0, tzinfo=dt.UTC)

    no_timestamp = apply_transition(
        _make_task(state="blocked"),
        "needs_ghost_triage",
        registry,
    )
    too_soon = apply_transition(
        _make_task(state="blocked"),
        "needs_ghost_triage",
        registry,
        blocked_at=now - dt.timedelta(hours=23),
        now=now,
    )
    escalated = apply_transition(
        _make_task(state="blocked"),
        "needs_ghost_triage",
        registry,
        blocked_at=dt.datetime(2026, 4, 19, 11, 0),
        now=now,
    )

    assert no_timestamp.state == "blocked"
    assert no_timestamp.guard_blocked == "policy"
    assert too_soon.state == "blocked"
    assert too_soon.guard_blocked == "policy"
    assert (
        too_soon.note
        == "Blocked escalation requires the task to remain blocked for at least 24 hours."
    )
    assert escalated.state == "needs_ghost_triage"
    assert escalated.escalation == "blocked_too_long"


def test_load_transition_policy_rejects_empty_transition_map(tmp_path: Path) -> None:
    schema_path = _repo_root() / "task_types" / "coding-task.schema.json"
    task_types_dir = tmp_path / "task_types"
    task_types_dir.mkdir()
    (task_types_dir / "coding-task.schema.json").write_text(
        schema_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (task_types_dir / "coding-task.policy.yaml").write_text(
        "\n".join(
            [
                'version: "1.0.0"',
                "hitl_required: true",
                "autonomy_tier: 3",
                "capabilities:",
                "  - read_repo",
                "body:",
                "  transitions: {}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    registry = _task_type_registry(task_types_dir)
    with pytest.raises(PolicyLoadError, match="transitions must not be empty"):
        apply_transition(
            _make_task(state="queued"),
            "claimed",
            registry,
            actor_capabilities=[CapabilityKey.READ_REPO],
        )
