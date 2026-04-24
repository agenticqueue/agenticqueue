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
class ContractDodItem:
    """Structured DoD proof metadata resolved for one task."""

    dod_id: str
    statement: str
    verification_method: str
    evidence_required: str
    acceptance_threshold: str
    is_legacy_adapter: bool = False


@dataclass(frozen=True)
class DodReport:
    """Structured report returned from a declarative DoD run."""

    checklist: tuple[DodChecklistResult, ...]
    checked_count: int
    partial_count: int
    unchecked_blocked_count: int
    unchecked_unmet_count: int


def resolve_contract_dod_items(task: TaskModel) -> tuple[ContractDodItem, ...]:
    """Return the canonical DoD proof items for one task."""

    raw_items = task.contract.get("dod_items")
    if isinstance(raw_items, list):
        structured_items: list[ContractDodItem] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                continue
            dod_id = _non_empty_text(raw_item.get("id"))
            statement = _non_empty_text(raw_item.get("statement"))
            verification_method = _non_empty_text(raw_item.get("verification_method"))
            evidence_required = _non_empty_text(raw_item.get("evidence_required"))
            acceptance_threshold = _non_empty_text(raw_item.get("acceptance_threshold"))
            if None in (
                dod_id,
                statement,
                verification_method,
                evidence_required,
                acceptance_threshold,
            ):
                continue
            assert dod_id is not None
            assert statement is not None
            assert verification_method is not None
            assert evidence_required is not None
            assert acceptance_threshold is not None
            structured_items.append(
                ContractDodItem(
                    dod_id=dod_id,
                    statement=statement,
                    verification_method=verification_method,
                    evidence_required=evidence_required,
                    acceptance_threshold=acceptance_threshold,
                )
            )
        if structured_items:
            return tuple(structured_items)

    statements = list(task.definition_of_done)
    if not statements:
        raw_checklist = task.contract.get("dod_checklist")
        if isinstance(raw_checklist, list):
            statements = [
                statement
                for raw_item in raw_checklist
                if isinstance(raw_item, str) and raw_item.strip()
                for statement in [raw_item.strip()]
            ]

    return tuple(
        ContractDodItem(
            dod_id=f"legacy-{index + 1}",
            statement=statement,
            verification_method="reviewer_check",
            evidence_required="Compatibility proof supplied at submission time.",
            acceptance_threshold="A terminal DoD result exists for this legacy item.",
            is_legacy_adapter=True,
        )
        for index, statement in enumerate(statements)
    )


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

    ordered_items = [item.statement for item in resolve_contract_dod_items(task)]
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


def _non_empty_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
