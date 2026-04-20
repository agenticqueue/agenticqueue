"""Declarative DoD runner for submission validation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agenticqueue_api.dod_checks import CHECK_HANDLERS, VALID_CHECK_TYPES
from agenticqueue_api.dod_checks.common import (
    ArtifactBundle,
    DodCheckContext,
    DodCheckResult,
    DodCheckValidationError,
    DodItemState,
    GitHubClientProtocol,
    coerce_check_definition,
)
from agenticqueue_api.models.task import TaskModel
from agenticqueue_api.task_type_registry import TaskTypeRegistry


@dataclass(frozen=True)
class DodChecklistResult:
    """Aggregated DoD state for one checklist item."""

    item: str
    state: DodItemState
    note: str


@dataclass(frozen=True)
class DodReport:
    """Structured report returned from a declarative DoD run."""

    checklist: tuple[DodChecklistResult, ...]
    checked_count: int
    partial_count: int
    unchecked_blocked_count: int
    unchecked_unmet_count: int


def run_dod_checks(
    task: TaskModel,
    submission_output: Mapping[str, Any],
    *,
    registry: TaskTypeRegistry,
    artifact_root: Path | None = None,
    github_client: GitHubClientProtocol | None = None,
) -> DodReport:
    """Run every declarative DoD check attached to a task contract."""

    raw_checks = task.contract.get("dod_checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise DodCheckValidationError(
            "Task contract must declare a non-empty 'dod_checks' list."
        )

    bundle = ArtifactBundle.from_output(submission_output, artifact_root=artifact_root)
    context = DodCheckContext(
        bundle=bundle,
        registry=registry,
        github_client=github_client,
    )

    grouped_results: dict[str, list[DodCheckResult]] = defaultdict(list)
    seen_order: list[str] = []
    for raw_check in raw_checks:
        definition = coerce_check_definition(raw_check)
        handler = CHECK_HANDLERS.get(definition.check_type)
        if handler is None:
            valid = ", ".join(VALID_CHECK_TYPES)
            raise DodCheckValidationError(
                f"Unknown DoD check type '{definition.check_type}'. Valid types: {valid}."
            )
        if definition.item not in grouped_results:
            seen_order.append(definition.item)
        grouped_results[definition.item].append(handler(definition, context))

    ordered_items = list(task.definition_of_done)
    for item in seen_order:
        if item not in ordered_items:
            ordered_items.append(item)

    checklist = tuple(
        _aggregate_item(item, grouped_results.get(item, [])) for item in ordered_items
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


def _aggregate_item(item: str, results: list[DodCheckResult]) -> DodChecklistResult:
    if not results:
        return DodChecklistResult(
            item=item,
            state=DodItemState.UNCHECKED_BLOCKED,
            note="No declarative checks configured for this DoD item.",
        )

    checked = sum(result.state == DodItemState.CHECKED for result in results)
    blocked = sum(result.state == DodItemState.UNCHECKED_BLOCKED for result in results)
    unmet = sum(result.state == DodItemState.UNCHECKED_UNMET for result in results)
    if checked == len(results):
        state = DodItemState.CHECKED
    elif checked > 0:
        state = DodItemState.PARTIAL
    elif blocked > 0 and unmet == 0:
        state = DodItemState.UNCHECKED_BLOCKED
    else:
        state = DodItemState.UNCHECKED_UNMET

    summary = f"{checked}/{len(results)} checks passed"
    notes = "; ".join(result.note for result in results)
    return DodChecklistResult(item=item, state=state, note=f"{summary}. {notes}")
