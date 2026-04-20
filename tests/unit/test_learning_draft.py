from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from agenticqueue_api.learnings import DraftLearning, LearningType, draft_learnings
from agenticqueue_api.models.run import RunModel
from agenticqueue_api.models.task import TaskModel


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _example_contract() -> dict[str, Any]:
    path = _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _task() -> TaskModel:
    contract = _example_contract()
    return TaskModel.model_validate(
        {
            "id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "task_type": "coding-task",
            "title": "Draft learning task",
            "state": "done",
            "description": "Generate deterministic learning drafts.",
            "contract": contract,
            "definition_of_done": contract["dod_checklist"],
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )


def _run(*, details: dict[str, Any]) -> RunModel:
    return RunModel.model_validate(
        {
            "id": str(uuid.uuid4()),
            "task_id": str(uuid.uuid4()),
            "actor_id": str(uuid.uuid4()),
            "status": "completed",
            "started_at": "2026-04-20T00:00:00+00:00",
            "ended_at": "2026-04-20T00:10:00+00:00",
            "summary": "Learning draft run",
            "details": details,
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:10:00+00:00",
        }
    )


def _submission() -> dict[str, Any]:
    contract = _example_contract()
    return {
        "output": copy.deepcopy(contract["output"]),
        "dod_results": [
            {"item": contract["dod_checklist"][0], "checked": True},
            {"item": contract["dod_checklist"][1], "checked": True},
        ],
        "had_failure": False,
        "had_block": False,
        "had_retry": False,
    }


def test_draft_learnings_returns_pitfall_after_two_validator_rejections() -> None:
    submission = _submission()
    submission["had_retry"] = True
    run = _run(
        details={
            "retry_count": 2,
            "attempts": [
                {
                    "status": "rejected",
                    "error_source": "validator",
                    "validator_errors": [
                        {
                            "field": "output.diff_url",
                            "message": "Field required",
                        }
                    ],
                },
                {
                    "status": "rejected",
                    "error_source": "validator",
                    "validator_errors": [
                        {
                            "field": "output.learnings.0.title",
                            "message": "Field required",
                        }
                    ],
                },
                {
                    "status": "succeeded",
                    "summary": "Submission accepted",
                },
            ],
        }
    )

    drafts = draft_learnings(_task(), run, submission)

    assert all(isinstance(draft, DraftLearning) for draft in drafts)
    pitfall = next(draft for draft in drafts if draft.type is LearningType.PITFALL)
    assert "output.diff_url" in pitfall.what_happened
    assert pitfall.title
    assert pitfall.action_rule
    assert pitfall.review_date == "2026-05-04"


@pytest.mark.parametrize(
    ("details", "submission_patch", "expected_type"),
    [
        (
            {
                "attempts": [
                    {
                        "status": "test_failed",
                        "failed_tests": ["tests/unit/test_validator.py::test_validate_submission_accepts_valid_payload"],
                    }
                ]
            },
            {},
            LearningType.TOOLING,
        ),
        (
            {
                "events": [
                    {"type": "blocked", "summary": "Waiting on dependency"},
                    {"type": "resolved", "summary": "Dependency landed"},
                ]
            },
            {"had_block": True},
            LearningType.DECISION_FOLLOWUP,
        ),
        (
            {
                "reviewer_corrections": [
                    "Keep the learning generator deterministic; no provider LLM calls."
                ]
            },
            {},
            LearningType.USER_PREFERENCE,
        ),
        (
            {"retry_count": 1},
            {"had_retry": True},
            LearningType.PROCESS_RULE,
        ),
    ],
)
def test_draft_learnings_maps_detector_signals_to_learning_types(
    details: dict[str, Any],
    submission_patch: dict[str, Any],
    expected_type: LearningType,
) -> None:
    submission = _submission()
    submission.update(submission_patch)

    drafts = draft_learnings(_task(), _run(details=details), submission)

    assert any(draft.type is expected_type for draft in drafts)


def test_draft_learnings_returns_empty_list_when_no_signal_is_present() -> None:
    drafts = draft_learnings(_task(), _run(details={"attempts": []}), _submission())

    assert drafts == []


def test_learning_package_reexports_all_learning_type_variants() -> None:
    assert {member.value for member in LearningType} == {
        "pitfall",
        "pattern",
        "decision-followup",
        "tooling",
        "repo-behavior",
        "user-preference",
        "process-rule",
    }
