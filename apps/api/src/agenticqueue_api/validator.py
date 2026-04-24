"""Submission validation for task outputs."""

from __future__ import annotations
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import ValidationError as JsonSchemaValidationError  # type: ignore[import-untyped]
from jsonschema.validators import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import ValidationError as PydanticValidationError

from agenticqueue_api.dod import DodReport, resolve_contract_dod_items, run_dod_checks
from agenticqueue_api.dod_checks.common import (
    DodCheckValidationError,
    DodItemState,
    GitHubClientProtocol,
)
from agenticqueue_api.models.task import TaskModel
from agenticqueue_api.schemas.submit import validate_task_completion_submission
from agenticqueue_api.task_type_registry import SchemaLoadError, TaskTypeRegistry

_MISSING_PROPERTY_RE = re.compile(r"'([^']+)' is a required property")
_RETRY_FLAGS = ("had_failure", "had_block", "had_retry")


@dataclass(frozen=True)
class ValidationIssue:
    """One structured validation rejection."""

    rule: str
    offending_field: str
    hint: str


@dataclass(frozen=True)
class ValidationResult:
    """Validation outcome for one task submission."""

    errors: tuple[ValidationIssue, ...]
    dod_report: DodReport | None = None

    @property
    def is_valid(self) -> bool:
        return not self.errors


class SubmissionValidator:
    """Validate task submissions against their task-type contract."""

    def __init__(
        self,
        registry: TaskTypeRegistry,
        *,
        artifact_root: Path | None = None,
        github_client: GitHubClientProtocol | None = None,
    ) -> None:
        self._registry = registry
        self._artifact_root = artifact_root
        self._github_client = github_client

    def validate_submission(
        self,
        task: TaskModel,
        submitted_output: Any,
    ) -> ValidationResult:
        if not isinstance(submitted_output, Mapping):
            return ValidationResult(
                errors=(
                    ValidationIssue(
                        rule="submission_payload_type",
                        offending_field="submission",
                        hint="Submit a JSON object with output artifacts and retry flags.",
                    ),
                )
            )

        try:
            normalized_submission = validate_task_completion_submission(
                submitted_output
            )
        except TypeError as error:
            return ValidationResult(
                errors=(
                    ValidationIssue(
                        rule="submission_payload_type",
                        offending_field="submission",
                        hint=str(error),
                    ),
                )
            )
        except PydanticValidationError as error:
            return ValidationResult(
                errors=tuple(
                    self._issue_from_submit_error(item) for item in error.errors()
                )
            )

        normalized_output = normalized_submission.model_dump(mode="json")
        issues: list[ValidationIssue] = []
        dod_report: DodReport | None = None
        retry_signal = self._has_retry_signal(normalized_output, issues)
        output = dict(normalized_output["output"])
        issues.extend(self._schema_issues(task.task_type, output))

        learnings = output["learnings"]
        if retry_signal and len(learnings) == 0:
            issues.append(
                ValidationIssue(
                    rule="learnings_required",
                    offending_field="output.learnings",
                    hint="Add at least one learning when the task had a failure, block, or retry.",
                )
            )
        issues.extend(self._dod_issues(task, normalized_output.get("dod_results")))

        if "dod_checks" in task.contract:
            try:
                dod_report = run_dod_checks(
                    task,
                    output,
                    registry=self._registry,
                    artifact_root=self._artifact_root,
                    github_client=self._github_client,
                )
            except DodCheckValidationError as error:
                issues.append(
                    ValidationIssue(
                        rule="dod_checks_invalid",
                        offending_field="contract.dod_checks",
                        hint=str(error),
                    )
                )
            else:
                issues.extend(self._dod_report_issues(dod_report))

        return ValidationResult(errors=tuple(issues), dod_report=dod_report)

    def _schema_issues(
        self,
        task_type: str,
        output: dict[str, Any],
    ) -> list[ValidationIssue]:
        try:
            definition = self._registry.get(task_type).schema
        except SchemaLoadError:
            return [
                ValidationIssue(
                    rule="task_type_schema_missing",
                    offending_field="task.task_type",
                    hint=f"Register task type '{task_type}' before validating submissions.",
                )
            ]

        schema = {
            **dict(definition["properties"]["output"]),
            "$schema": definition.get(
                "$schema", "https://json-schema.org/draft/2020-12/schema"
            ),
            "$defs": definition.get("$defs", {}),
        }
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(output), key=self._schema_error_sort_key)
        return [self._issue_from_schema_error(error) for error in errors]

    def _dod_issues(
        self,
        task: TaskModel,
        dod_results: Any,
    ) -> list[ValidationIssue]:
        expected_items = resolve_contract_dod_items(task)
        if not expected_items:
            return []

        by_id = {item.dod_id: item for item in expected_items}
        by_statement = {item.statement: item for item in expected_items}
        issues: list[ValidationIssue] = []
        seen_ids: set[str] = set()
        unknown_id_seen = False
        for index, result in enumerate(dod_results or []):
            field_prefix = f"dod_results.{index}"
            dod_id = result.get("dod_id")
            matched = by_id.get(dod_id) or by_statement.get(dod_id)
            if matched is None:
                unknown_id_seen = True
                issues.append(
                    ValidationIssue(
                        rule="dod_result_unknown_id",
                        offending_field=f"{field_prefix}.dod_id",
                        hint="Reference one of the task's structured DoD ids or compatibility statements.",
                    )
                )
                continue

            seen_ids.add(matched.dod_id)
            status = result.get("status")
            evidence = result.get("evidence")
            failure_reason = result.get("failure_reason")
            if status == "passed" and (
                not isinstance(evidence, list) or len(evidence) == 0
            ):
                issues.append(
                    ValidationIssue(
                        rule="dod_result_evidence_required",
                        offending_field=f"{field_prefix}.evidence",
                        hint=f"Add at least one evidence pointer for '{matched.statement}'.",
                    )
                )
            if status in {"failed", "blocked", "not_applicable"} and not (
                isinstance(failure_reason, str) and failure_reason.strip()
            ):
                issues.append(
                    ValidationIssue(
                        rule="dod_result_reason_required",
                        offending_field=f"{field_prefix}.failure_reason",
                        hint=(
                            f"Explain why '{matched.statement}' did not pass before completing the task."
                        ),
                    )
                )

        if not unknown_id_seen:
            missing_items = [
                item for item in expected_items if item.dod_id not in seen_ids
            ]
        else:
            missing_items = []

        for item in missing_items:
            issues.append(
                ValidationIssue(
                    rule="dod_result_missing",
                    offending_field="dod_results",
                    hint=f"Provide a terminal DoD result for '{item.statement}' ({item.dod_id}).",
                )
            )
        return issues

    def _dod_report_issues(self, dod_report: DodReport) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for index, result in enumerate(dod_report.checklist):
            if result.state == DodItemState.CHECKED:
                continue
            issues.append(
                ValidationIssue(
                    rule=f"dod_{result.state}",
                    offending_field=f"definition_of_done.{index}",
                    hint=f"{result.item}: {result.note}",
                )
            )
        return issues

    def _has_retry_signal(
        self,
        submitted_output: Mapping[str, Any],
        issues: list[ValidationIssue],
    ) -> bool:
        del issues
        return any(
            bool(submitted_output.get(flag_name, False)) for flag_name in _RETRY_FLAGS
        )

    @staticmethod
    def _schema_error_sort_key(
        error: JsonSchemaValidationError,
    ) -> tuple[tuple[str, ...], str]:
        return tuple(str(part) for part in error.path), error.message

    def _issue_from_schema_error(
        self,
        error: JsonSchemaValidationError,
    ) -> ValidationIssue:
        path = ["output", *[str(part) for part in error.path]]
        hint = error.message
        missing_match = _MISSING_PROPERTY_RE.search(error.message)
        if error.validator == "required" and missing_match is not None:
            missing_field = missing_match.group(1)
            path.append(missing_field)
            hint = f"Provide '{missing_field}' in the submission output."
        return ValidationIssue(
            rule=f"schema.{error.validator}",
            offending_field=".".join(path),
            hint=hint,
        )

    def _issue_from_submit_error(self, error: Mapping[str, Any]) -> ValidationIssue:
        location = [str(part) for part in error.get("loc", ()) if part != "__root__"]
        offending_field = ".".join(location) if location else "submission"
        error_type = str(error.get("type", "validation_error"))

        if error_type == "extra_forbidden":
            rule = "submission.extra_forbidden"
            hint = f"Remove unexpected field '{offending_field}'."
        elif error_type in {"bool_type", "int_type", "string_type", "date_type"}:
            rule = f"submission.{error_type}"
            hint = f"Provide the expected JSON type for '{offending_field}'."
        elif error_type in {"string_too_long", "list_too_long", "dict_too_long"}:
            rule = f"submission.{error_type}"
            hint = f"Reduce the size of '{offending_field}'."
        elif error_type == "value_error":
            rule = "submission.value_error"
            hint = str(error.get("msg", "Submission payload failed validation."))
        else:
            rule = f"submission.{error_type}"
            hint = str(error.get("msg", "Submission payload failed validation."))

        return ValidationIssue(
            rule=rule,
            offending_field=offending_field,
            hint=hint,
        )
