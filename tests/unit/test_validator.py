from __future__ import annotations

import copy
import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Any

from agenticqueue_api.config import get_task_types_dir
from agenticqueue_api.schemas.submit import (
    MAX_SUBMISSION_DEPTH,
    validate_task_completion_submission,
)
from agenticqueue_api.models.task import TaskModel
from agenticqueue_api.task_type_registry import TaskTypeRegistry
from agenticqueue_api.validator import SubmissionValidator
from jsonschema.validators import Draft202012Validator  # type: ignore[import-untyped]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _example_contract() -> dict[str, Any]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _structured_dod_items(contract: dict[str, Any]) -> list[dict[str, str]]:
    verification_methods = ("test", "test", "code_inspection")
    return [
        {
            "id": f"dod-{index + 1}",
            "statement": item,
            "verification_method": (
                verification_methods[index]
                if index < len(verification_methods)
                else "artifact"
            ),
            "evidence_required": f"Evidence proving: {item}",
            "acceptance_threshold": f"Proof for '{item}' is present and valid.",
        }
        for index, item in enumerate(contract["dod_checklist"])
    ]


def _make_task(
    *,
    task_type: str = "coding-task",
    declarative_dod: bool = False,
    structured_dod: bool = False,
) -> TaskModel:
    contract = _example_contract()
    if structured_dod:
        contract["dod_items"] = _structured_dod_items(contract)
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
            {"item": item, "checked": True} for item in contract["dod_checklist"]
        ],
        "had_failure": False,
        "had_block": False,
        "had_retry": False,
    }


def _make_structured_submission() -> dict[str, Any]:
    contract = _example_contract()
    contract["dod_items"] = _structured_dod_items(contract)
    output = copy.deepcopy(contract["output"])
    evidence_uri = output["artifacts"][0]["uri"]
    return {
        "output": output,
        "dod_results": [
            {
                "dod_id": item["id"],
                "status": "passed",
                "evidence": [evidence_uri],
                "summary": item["statement"],
                "failure_reason": None,
                "next_action": None,
            }
            for item in contract["dod_items"]
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


def test_validate_task_completion_submission_adapts_legacy_dod_results() -> None:
    submission = _make_submission()
    submission["dod_results"][1]["checked"] = False
    normalized = validate_task_completion_submission(submission).model_dump(mode="json")

    assert normalized["dod_results"] == [
        {
            "dod_id": _example_contract()["dod_checklist"][0],
            "status": "passed",
            "evidence": ["legacy-adapter://checked"],
            "summary": "Legacy DoD item marked checked.",
            "failure_reason": None,
            "next_action": None,
        },
        {
            "dod_id": _example_contract()["dod_checklist"][1],
            "status": "failed",
            "evidence": [],
            "summary": "Legacy DoD item was left unchecked.",
            "failure_reason": "Legacy DoD item was not checked in the submission.",
            "next_action": "Provide proof for the DoD item or resubmit with the correct status.",
        },
        {
            "dod_id": _example_contract()["dod_checklist"][2],
            "status": "passed",
            "evidence": ["legacy-adapter://checked"],
            "summary": "Legacy DoD item marked checked.",
            "failure_reason": None,
            "next_action": None,
        },
    ]


def test_validate_submission_accepts_structured_dod_results() -> None:
    result = _validator().validate_submission(
        _make_task(structured_dod=True),
        _make_structured_submission(),
    )

    assert result.is_valid is True
    assert result.errors == ()
    assert result.dod_report is None


def test_validate_submission_rejects_missing_structured_dod_result() -> None:
    submission = _make_structured_submission()
    submission["dod_results"] = submission["dod_results"][:-1]

    result = _validator().validate_submission(
        _make_task(structured_dod=True),
        submission,
    )

    assert result.is_valid is False
    assert [error.rule for error in result.errors] == ["dod_result_missing"]


def test_validate_submission_rejects_unknown_structured_dod_id() -> None:
    submission = _make_structured_submission()
    submission["dod_results"][0]["dod_id"] = "dod-404"

    result = _validator().validate_submission(
        _make_task(structured_dod=True),
        submission,
    )

    assert result.is_valid is False
    assert [error.rule for error in result.errors] == ["dod_result_unknown_id"]


def test_validate_submission_rejects_failed_structured_dod_without_reason() -> None:
    submission = _make_structured_submission()
    submission["dod_results"][0]["status"] = "failed"
    submission["dod_results"][0]["failure_reason"] = None

    result = _validator().validate_submission(
        _make_task(structured_dod=True),
        submission,
    )

    assert result.is_valid is False
    assert [error.rule for error in result.errors] == ["dod_result_reason_required"]


def test_validate_submission_rejects_passed_structured_dod_without_evidence() -> None:
    submission = _make_structured_submission()
    submission["dod_results"][0]["evidence"] = []

    result = _validator().validate_submission(
        _make_task(structured_dod=True),
        submission,
    )

    assert result.is_valid is False
    assert [error.rule for error in result.errors] == ["dod_result_evidence_required"]


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
    assert result.errors[0].rule == "submission.missing"
    assert result.errors[0].offending_field == "output.diff_url"
    assert result.errors[0].hint == "Field required"


def test_validate_submission_reports_semantic_artifact_and_dod_errors() -> None:
    submission = _make_submission()
    submission["output"]["artifacts"] = []
    submission["dod_results"] = [
        {"item": _make_task().definition_of_done[0], "checked": False}
    ]

    result = _validator().validate_submission(_make_task(), submission)

    assert result.is_valid is False
    assert [error.rule for error in result.errors] == ["submission.too_short"]
    assert result.errors[0].offending_field == "output.artifacts"


def test_validate_submission_requires_learnings_after_retry_signal() -> None:
    submission = _make_submission()
    submission["had_retry"] = True
    submission["output"]["learnings"] = []

    result = _validator().validate_submission(_make_task(), submission)

    assert result.is_valid is False
    assert [error.rule for error in result.errors] == ["learnings_required"]


def test_validate_submission_rejects_bad_dod_shapes() -> None:
    submission = _make_submission()
    submission["dod_results"] = [
        "not-an-object",
        {"item": "", "checked": False},
        {"item": submission["output"]["artifacts"][0]["uri"], "checked": "yes"},
        {"item": "Unknown DoD", "checked": True},
    ]

    result = _validator().validate_submission(_make_task(), submission)

    assert result.is_valid is False
    rules = {error.rule for error in result.errors}
    assert "submission.model_type" in rules
    assert "submission.string_too_short" in rules
    assert "submission.missing" in rules
    assert "submission.extra_forbidden" in rules


def test_validate_submission_rejects_missing_output_and_dod_results() -> None:
    result = _validator().validate_submission(
        _make_task(),
        {"had_failure": False, "had_block": False, "had_retry": False},
    )

    assert result.is_valid is False
    assert [error.rule for error in result.errors] == [
        "submission.missing",
        "submission.missing",
    ]
    assert [error.offending_field for error in result.errors] == [
        "output",
        "dod_results",
    ]


def test_validate_submission_rejects_stringified_retry_flag_from_strict_schema() -> (
    None
):
    submission = _make_submission()
    submission["had_retry"] = "true"

    result = _validator().validate_submission(_make_task(), submission)

    assert result.is_valid is False
    assert [error.rule for error in result.errors] == ["submission.bool_type"]
    assert result.errors[0].offending_field == "had_retry"


def test_validate_submission_rejects_extra_fields_from_strict_schema() -> None:
    submission = _make_submission()
    submission["output"]["learnings"][0]["unexpected"] = "nope"

    result = _validator().validate_submission(_make_task(), submission)

    assert result.is_valid is False
    assert [error.rule for error in result.errors] == ["submission.extra_forbidden"]
    assert result.errors[0].offending_field == "output.learnings.0.unexpected"


def test_validate_submission_rejects_unserializable_payload() -> None:
    submission = _make_submission()
    submission["output"]["artifacts"][0]["details"] = {"bad": {1, 2}}

    result = _validator().validate_submission(_make_task(), submission)

    assert result.is_valid is False
    assert [error.rule for error in result.errors] == ["submission_payload_type"]


def test_validate_submission_rejects_excessive_depth_from_strict_schema() -> None:
    submission = _make_submission()
    nested: dict[str, object] = {"level_0": "done"}
    for index in range(MAX_SUBMISSION_DEPTH + 1):
        nested = {f"level_{index + 1}": nested}
    submission["output"]["artifacts"][0]["details"] = nested

    result = _validator().validate_submission(_make_task(), submission)

    assert result.is_valid is False
    assert [error.rule for error in result.errors] == ["submission.value_error"]


def test_validate_submission_rejects_overlong_strings_from_strict_schema() -> None:
    submission = _make_submission()
    submission["output"]["learnings"][0]["title"] = "x" * 1100

    result = _validator().validate_submission(_make_task(), submission)

    assert result.is_valid is False
    assert [error.rule for error in result.errors] == ["submission.string_too_long"]


def test_validate_submission_rejects_unknown_dod_ids() -> None:
    submission = _make_submission()
    submission["dod_results"] = [{"item": "Unknown DoD", "checked": False}]

    result = _validator().validate_submission(_make_task(), submission)

    assert result.is_valid is False
    rules = {error.rule for error in result.errors}
    assert "dod_result_unknown_id" in rules


def test_validator_helper_maps_required_schema_errors() -> None:
    validator = _validator()
    schema_error = next(
        Draft202012Validator(
            {
                "type": "object",
                "required": ["diff_url"],
            }
        ).iter_errors({})
    )

    assert validator._schema_error_sort_key(schema_error) == (
        tuple(),
        "'diff_url' is a required property",
    )
    issue = validator._issue_from_schema_error(schema_error)

    assert issue.rule == "schema.required"
    assert issue.offending_field == "output.diff_url"
    assert issue.hint == "Provide 'diff_url' in the submission output."


def test_validator_helper_preserves_non_required_schema_errors() -> None:
    validator = _validator()
    schema_error = next(
        Draft202012Validator(
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "minLength": 3,
                    }
                },
            }
        ).iter_errors({"kind": "x"})
    )

    issue = validator._issue_from_schema_error(schema_error)

    assert issue.rule == "schema.minLength"
    assert issue.offending_field == "output.kind"
    assert issue.hint == "'x' is too short"


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
    assert result.errors[0].hint == "shell exec disabled by task policy"


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
