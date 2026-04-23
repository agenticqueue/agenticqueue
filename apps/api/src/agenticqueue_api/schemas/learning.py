"""Reusable learning schema definitions and JSON Schema emission."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ShortText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=255),
]
MediumText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=1024),
]
LongText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=4096),
]
PathText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=2048),
]
DateText = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=10,
        max_length=10,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    ),
]


class LearningType(StrEnum):
    """Canonical learning kinds."""

    PITFALL = "pitfall"
    PATTERN = "pattern"
    DECISION_FOLLOWUP = "decision-followup"
    TOOLING = "tooling"
    REPO_BEHAVIOR = "repo-behavior"
    USER_PREFERENCE = "user-preference"
    PROCESS_RULE = "process-rule"


class LearningScope(StrEnum):
    """Promotion scope for one learning."""

    TASK = "task"
    PROJECT = "project"
    GLOBAL = "global"


class LearningConfidence(StrEnum):
    """Confidence levels for one learning."""

    TENTATIVE = "tentative"
    CONFIRMED = "confirmed"
    VALIDATED = "validated"


class LearningStatus(StrEnum):
    """Lifecycle states for one learning."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


class LearningSchemaModel(BaseModel):
    """Standalone 13-field learning schema."""

    model_config = ConfigDict(extra="forbid")

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


def learning_schema_json() -> dict[str, Any]:
    """Return the canonical JSON Schema for the learning payload."""

    schema = LearningSchemaModel.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "learning"
    return schema


def default_learning_schema_path() -> Path:
    """Return the checked-in path for the standalone learning schema."""

    return Path(__file__).resolve().parents[5] / "schemas" / "learning.schema.json"


def write_learning_schema(path: Path | None = None) -> Path:
    """Write the canonical learning schema to disk."""

    target = path or default_learning_schema_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(learning_schema_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target
