"""Retry-policy helpers shared across task surfaces."""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from typing import Any

from sqlalchemy.orm import Session

from agenticqueue_api.compiler import resolve_task_policy
from agenticqueue_api.config import get_reload_enabled, get_task_types_dir
from agenticqueue_api.models.task import TaskModel, TaskRecord
from agenticqueue_api.task_type_registry import TaskTypeRegistry
from agenticqueue_api.transitions import TransitionPolicy, load_transition_policy

_ATTEMPT_METRICS: Counter[str] = Counter()


@lru_cache(maxsize=1)
def _cached_task_type_registry() -> TaskTypeRegistry:
    registry = TaskTypeRegistry(
        get_task_types_dir(),
        reload_enabled=get_reload_enabled(),
    )
    registry.load()
    return registry


def resolve_max_attempts(
    *,
    task_type: str,
    policy_body: dict[str, Any],
    default: int,
) -> int:
    """Resolve the effective retry threshold for one task type."""

    overrides = policy_body.get("max_attempts_per_task_type")
    if isinstance(overrides, dict):
        value = overrides.get(task_type)
        if isinstance(value, bool):
            return default
        if isinstance(value, int) and value >= 1:
            return value
        if isinstance(value, str):
            normalized = value.strip()
            if normalized.isdigit() and int(normalized) >= 1:
                return int(normalized)
    return default


def remaining_attempts(*, attempt_count: int, max_attempts: int) -> int:
    """Return the remaining retries before the task enters the DLQ."""

    return max(max_attempts - attempt_count, 0)


def effective_max_attempts(
    session: Session,
    task_record: TaskRecord,
    *,
    task_type_registry: TaskTypeRegistry | None = None,
    transition_policy: TransitionPolicy | None = None,
) -> int:
    """Resolve the effective retry threshold for one task record."""

    registry = task_type_registry or _cached_task_type_registry()
    base_policy = transition_policy or load_transition_policy(
        task_record.task_type,
        registry,
    )
    resolved_policy = resolve_task_policy(
        session,
        task_record,
        task_type_registry=registry,
    )
    return resolve_max_attempts(
        task_type=task_record.task_type,
        policy_body=resolved_policy.body,
        default=base_policy.max_retries,
    )


def with_retry_fields(
    session: Session,
    task_record: TaskRecord,
    *,
    task_type_registry: TaskTypeRegistry | None = None,
    transition_policy: TransitionPolicy | None = None,
) -> TaskModel:
    """Return a task model with retry metadata materialized."""

    task = TaskModel.model_validate(task_record)
    max_attempts = effective_max_attempts(
        session,
        task_record,
        task_type_registry=task_type_registry,
        transition_policy=transition_policy,
    )
    return task.model_copy(
        update={
            "max_attempts": max_attempts,
            "remaining_attempts": remaining_attempts(
                attempt_count=task.attempt_count,
                max_attempts=max_attempts,
            ),
        }
    )


def increment_attempt_metric(outcome: str) -> None:
    """Increment the in-process retry metric for one outcome."""

    _ATTEMPT_METRICS[outcome] += 1


def attempt_metric_value(outcome: str) -> int:
    """Return the in-process retry metric value for one outcome."""

    return int(_ATTEMPT_METRICS[outcome])


def reset_attempt_metrics() -> None:
    """Clear the in-process retry counters."""

    _ATTEMPT_METRICS.clear()
