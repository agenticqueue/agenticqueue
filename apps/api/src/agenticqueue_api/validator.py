"""Submission validation for task outputs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jsonschema import ValidationError as JsonSchemaValidationError  # type: ignore[import-untyped]
from jsonschema.validators import Draft202012Validator  # type: ignore[import-untyped]

from agenticqueue_api.models.task import TaskModel
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

    @property
    def is_valid(self) -> bool:
        return not self.errors


class SubmissionValidator:
    """Validate task submissions against their task-type contract."""

    def __init__(self, registry: TaskTypeRegistry) -> None:
        self._registry = registry

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
                        hint="Submit a JSON object with output, dod_results, and retry flags.",
                    ),
                )
            )

        issues: list[ValidationIssue] = []
        retry_signal = self._has_retry_signal(submitted_output, issues)
        output = submitted_output.get("output")
        if not isinstance(output, Mapping):
            issues.append(
                ValidationIssue(
                    rule="submission_output_type",
                    offending_field="output",
                    hint="Provide an 'output' object that matches the task type schema.",
                )
            )
        else:
            issues.extend(self._schema_issues(task.task_type, dict(output)))
            artifacts = output.get("artifacts")
            if isinstance(artifacts, list) and len(artifacts) == 0:
                issues.append(
                    ValidationIssue(
                        rule="artifacts_required",
                        offending_field="output.artifacts",
                        hint="Attach at least one artifact to the submission output.",
                    )
                )

            learnings = output.get("learnings")
            if retry_signal and isinstance(learnings, list) and len(learnings) == 0:
                issues.append(
                    ValidationIssue(
                        rule="learnings_required",
                        offending_field="output.learnings",
                        hint="Add at least one learning when the task had a failure, block, or retry.",
                    )
                )
        issues.extend(self._dod_issues(task, submitted_output.get("dod_results")))
        return ValidationResult(errors=tuple(issues))

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
        if not isinstance(dod_results, list):
            return [
                ValidationIssue(
                    rule="dod_results_type",
                    offending_field="dod_results",
                    hint="Provide a list of {item, checked} DoD results.",
                )
            ]

        expected_items = set(task.definition_of_done)
        checked_any = False
        issues: list[ValidationIssue] = []
        for index, result in enumerate(dod_results):
            field_prefix = f"dod_results.{index}"
            if not isinstance(result, Mapping):
                issues.append(
                    ValidationIssue(
                        rule="dod_result_type",
                        offending_field=field_prefix,
                        hint="Each DoD result must be an object with item and checked fields.",
                    )
                )
                continue

            item = result.get("item")
            checked = result.get("checked")
            if not isinstance(item, str) or not item.strip():
                issues.append(
                    ValidationIssue(
                        rule="dod_result_item_required",
                        offending_field=f"{field_prefix}.item",
                        hint="Provide the exact DoD item text for this result.",
                    )
                )
                continue
            if not isinstance(checked, bool):
                issues.append(
                    ValidationIssue(
                        rule="dod_result_checked_type",
                        offending_field=f"{field_prefix}.checked",
                        hint="Use true or false for each DoD result.",
                    )
                )
                continue
            if expected_items and item not in expected_items:
                issues.append(
                    ValidationIssue(
                        rule="dod_result_unknown_item",
                        offending_field=f"{field_prefix}.item",
                        hint="Reference one of the task's definition_of_done entries.",
                    )
                )
                continue
            if checked:
                checked_any = True

        if expected_items and not checked_any:
            issues.append(
                ValidationIssue(
                    rule="dod_checked_required",
                    offending_field="dod_results",
                    hint="Mark at least one DoD item as checked before submitting.",
                )
            )
        return issues

    def _has_retry_signal(
        self,
        submitted_output: Mapping[str, Any],
        issues: list[ValidationIssue],
    ) -> bool:
        had_signal = False
        for flag_name in _RETRY_FLAGS:
            flag_value = submitted_output.get(flag_name, False)
            if not isinstance(flag_value, bool):
                issues.append(
                    ValidationIssue(
                        rule="retry_flag_type",
                        offending_field=flag_name,
                        hint=f"Use true or false for '{flag_name}'.",
                    )
                )
                continue
            had_signal = had_signal or flag_value
        return had_signal

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
