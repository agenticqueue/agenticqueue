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


def _make_task(
    *,
    task_type: str = "coding-task",
    declarative_dod: bool = False,
) -> TaskModel:
    contract = _example_contract()
    if not declarative_dod:
        contract.pop("dod_checks", None)
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


def _write(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def _make_submission(tmp_path: Path | None = None) -> dict[str, Any]:
    contract = _example_contract()
    output = copy.deepcopy(contract["output"])
    if tmp_path is not None:
        _write(
            tmp_path / "artifacts" / "diffs" / "aq-52.patch",
            "@@ /v1/tasks/{id}\n+ test\n",
        )
        _write(
            tmp_path / "artifacts" / "tests" / "aq-52-pytest.txt",
            "test_get_task_returns_200\n"
            "test_missing_task_returns_404\n"
            "4 passed in 0.15s\n",
        )

    return {
        "output": output,
        "dod_results": [
            {"item": contract["dod_checklist"][0], "checked": True},
            {"item": contract["dod_checklist"][1], "checked": False},
        ],
        "had_failure": False,
        "had_block": False,
        "had_retry": False,
    }


def _validator(*, artifact_root: Path | None = None) -> SubmissionValidator:
    registry = TaskTypeRegistry(get_task_types_dir())
    registry.load()
    return SubmissionValidator(registry, artifact_root=artifact_root)


def test_validate_submission_accepts_valid_payload() -> None:
    result = _validator().validate_submission(_make_task(), _make_submission())

    assert result.is_valid is True
    assert result.errors == ()
    assert result.dod_report is None


def test_validate_submission_returns_declarative_dod_report(tmp_path: Path) -> None:
    result = _validator(artifact_root=tmp_path).validate_submission(
        _make_task(declarative_dod=True),
        _make_submission(tmp_path),
    )

    assert result.is_valid is True
    assert result.errors == ()
    assert result.dod_report is not None
    assert [item.state for item in result.dod_report.checklist] == [
        "checked",
        "checked",
        "checked",
    ]
    assert result.dod_report.checked_count == 3


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


def test_validate_submission_rejects_invalid_declarative_dod_contract(
    tmp_path: Path,
) -> None:
    task = _make_task(declarative_dod=True)
    task.contract["dod_checks"] = [
        {
            "item": task.definition_of_done[0],
            "type": "shell",
            "cmd": "pytest",
        }
    ]

    result = _validator().validate_submission(task, _make_submission(tmp_path))

    assert result.is_valid is False
    assert result.errors[0].rule == "dod_checks_invalid"
    assert result.errors[0].hint == "shell exec disabled; see ADR-AQ-012"


def test_validate_submission_reports_unmet_declarative_dod_items(
    tmp_path: Path,
) -> None:
    task = _make_task(declarative_dod=True)
    task.contract["dod_checks"][0]["pattern"] = "missing-pattern"

    result = _validator(artifact_root=tmp_path).validate_submission(
        task, _make_submission(tmp_path)
    )

    assert result.is_valid is False
    assert [error.rule for error in result.errors] == ["dod_unchecked_unmet"]
    assert result.dod_report is not None
