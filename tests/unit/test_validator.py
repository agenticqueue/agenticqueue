from __future__ import annotations

import copy
import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Any

from agenticqueue_api.config import get_task_types_dir
from agenticqueue_api.models.task import TaskModel
from agenticqueue_api.task_type_registry import TaskTypeRegistry
from agenticqueue_api.validator import SubmissionValidator


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _example_contract() -> dict[str, Any]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _make_task(*, task_type: str = "coding-task") -> TaskModel:
    contract = _example_contract()
    return TaskModel.model_validate(
        {
            "id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "task_type": task_type,
            "title": "Validator task",
            "state": "queued",
            "description": "Validator test task",
            "contract": contract,
            "definition_of_done": contract["dod_checklist"],
            "created_at": dt.datetime(2026, 4, 20, tzinfo=dt.UTC).isoformat(),
            "updated_at": dt.datetime(2026, 4, 20, tzinfo=dt.UTC).isoformat(),
        }
    )


def _make_submission() -> dict[str, Any]:
    contract = _example_contract()
    return {
        "output": copy.deepcopy(contract["output"]),
        "dod_results": [
            {"item": contract["dod_checklist"][0], "checked": True},
            {"item": contract["dod_checklist"][1], "checked": False},
        ],
        "had_failure": False,
        "had_block": False,
        "had_retry": False,
    }


def _validator() -> SubmissionValidator:
    registry = TaskTypeRegistry(get_task_types_dir())
    registry.load()
    return SubmissionValidator(registry)


def test_validate_submission_accepts_valid_payload() -> None:
    result = _validator().validate_submission(_make_task(), _make_submission())

    assert result.is_valid is True
    assert result.errors == ()


def test_validate_submission_rejects_non_mapping_payload() -> None:
    result = _validator().validate_submission(_make_task(), [])

    assert result.is_valid is False
    assert result.errors[0].rule == "submission_payload_type"
    assert result.errors[0].offending_field == "submission"


def test_validate_submission_rejects_unknown_task_type() -> None:
    result = _validator().validate_submission(
        _make_task(task_type="review-task"),
        _make_submission(),
    )

    assert result.is_valid is False
    assert result.errors[0].rule == "task_type_schema_missing"
    assert result.errors[0].offending_field == "task.task_type"


def test_validate_submission_reports_structured_schema_errors() -> None:
    submission = _make_submission()
    submission["output"].pop("diff_url")

    result = _validator().validate_submission(_make_task(), submission)

    assert result.is_valid is False
    assert len(result.errors) == 1
    assert result.errors[0].rule == "schema.required"
    assert result.errors[0].offending_field == "output.diff_url"
    assert result.errors[0].hint == "Provide 'diff_url' in the submission output."


def test_validate_submission_reports_semantic_artifact_and_dod_errors() -> None:
    submission = _make_submission()
    submission["output"]["artifacts"] = []
    submission["dod_results"] = [
        {"item": _make_task().definition_of_done[0], "checked": False}
    ]

    result = _validator().validate_submission(_make_task(), submission)

    assert result.is_valid is False
    rules = {error.rule for error in result.errors}
    assert "schema.minItems" in rules
    assert "artifacts_required" in rules
    assert "dod_checked_required" in rules


def test_validate_submission_requires_learnings_after_retry_signal() -> None:
    submission = _make_submission()
    submission["had_retry"] = True
    submission["output"]["learnings"] = []

    result = _validator().validate_submission(_make_task(), submission)

    assert result.is_valid is False
    assert [error.rule for error in result.errors] == ["learnings_required"]


def test_validate_submission_rejects_bad_flag_and_dod_shapes() -> None:
    submission = _make_submission()
    submission["had_failure"] = "yes"
    submission["dod_results"] = [
        "not-an-object",
        {"item": "", "checked": False},
        {"item": submission["output"]["artifacts"][0]["uri"], "checked": "yes"},
        {"item": "Unknown DoD", "checked": True},
    ]

    result = _validator().validate_submission(_make_task(), submission)

    assert result.is_valid is False
    rules = {error.rule for error in result.errors}
    assert "retry_flag_type" in rules
    assert "dod_result_type" in rules
    assert "dod_result_item_required" in rules
    assert "dod_result_checked_type" in rules
    assert "dod_result_unknown_item" in rules
    assert "dod_checked_required" in rules


def test_validate_submission_rejects_missing_output_and_dod_results() -> None:
    result = _validator().validate_submission(
        _make_task(),
        {"had_failure": False, "had_block": False, "had_retry": False},
    )

    assert result.is_valid is False
    assert [error.rule for error in result.errors] == [
        "submission_output_type",
        "dod_results_type",
    ]
