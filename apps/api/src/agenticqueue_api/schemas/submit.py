"""Strict submit-payload schemas used by the contract validator."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Final, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic import model_validator

MAX_SUBMISSION_DEPTH: Final = 10

ShortText = Annotated[str, StringConstraints(min_length=1, max_length=255)]
MediumText = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
PathText = Annotated[str, StringConstraints(min_length=1, max_length=2048)]
DateText = Annotated[
    str,
    StringConstraints(
        min_length=10,
        max_length=10,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    ),
]
ArtifactKind = Annotated[str, StringConstraints(min_length=1, max_length=64)]
LearningType = Literal[
    "pitfall",
    "pattern",
    "decision-followup",
    "tooling",
    "repo-behavior",
    "user-preference",
    "process-rule",
]
LearningScope = Literal["task", "project", "global"]
LearningConfidence = Literal["tentative", "confirmed", "validated"]
LearningStatus = Literal["active", "superseded", "expired"]
DetailKey = Annotated[str, StringConstraints(min_length=1, max_length=64)]
JsonScalar: TypeAlias = str | int | float | bool | None
JsonDetails: TypeAlias = dict[
    DetailKey,
    JsonScalar | list[JsonScalar] | dict[DetailKey, JsonScalar],
]


def submission_payload_depth(value: Any) -> int:
    """Return the maximum nested container depth for one submission payload."""

    if isinstance(value, Mapping):
        if not value:
            return 1
        return 1 + max(submission_payload_depth(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if not value:
            return 1
        return 1 + max(submission_payload_depth(item) for item in value)
    return 0


class StrictSchemaModel(BaseModel):
    """Strict base model for request payloads parsed from JSON."""

    model_config = ConfigDict(strict=True, extra="forbid")


class SubmitArtifactModel(StrictSchemaModel):
    """One execution artifact in a task-completion submission."""

    kind: ArtifactKind
    uri: PathText
    details: JsonDetails = Field(default_factory=dict, max_length=16)


class SubmitLearningModel(StrictSchemaModel):
    """One structured learning embedded in a task completion."""

    title: MediumText
    type: LearningType
    what_happened: LongText
    what_learned: LongText
    action_rule: LongText
    applies_when: MediumText
    does_not_apply_when: MediumText
    evidence: list[PathText] = Field(min_length=1, max_length=16)
    scope: LearningScope
    confidence: LearningConfidence
    status: LearningStatus
    owner: ShortText
    review_date: DateText


class SubmitOutputModel(StrictSchemaModel):
    """Typed output block from a task completion."""

    diff_url: PathText
    test_report: PathText
    artifacts: list[SubmitArtifactModel] = Field(min_length=1, max_length=32)
    learnings: list[SubmitLearningModel] = Field(default_factory=list, max_length=16)


class SubmitDodResultModel(StrictSchemaModel):
    """One submitted DoD checklist result."""

    item: MediumText
    checked: bool


class TaskCompletionSubmission(StrictSchemaModel):
    """Strict envelope for the validator's task completion input."""

    output: SubmitOutputModel
    dod_results: list[SubmitDodResultModel] = Field(min_length=1, max_length=64)
    had_failure: bool = False
    had_block: bool = False
    had_retry: bool = False

    @model_validator(mode="before")
    @classmethod
    def _validate_depth(cls, value: Any) -> Any:
        depth = submission_payload_depth(value)
        if depth > MAX_SUBMISSION_DEPTH:
            raise ValueError(
                f"Submission payload exceeds maximum nesting depth of {MAX_SUBMISSION_DEPTH}"
            )
        return value


def validate_task_completion_submission(payload: Any) -> TaskCompletionSubmission:
    """Parse one JSON-like payload using strict JSON semantics."""

    return TaskCompletionSubmission.model_validate_json(json.dumps(payload))
