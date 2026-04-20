from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import ValidationError as JsonSchemaValidationError  # type: ignore[import-untyped]
from jsonschema.validators import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import ValidationError

from agenticqueue_api.schemas.learning import (
    LearningSchemaModel,
    learning_schema_json,
    write_learning_schema,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _example_learning() -> dict[str, object]:
    contract_path = (
        _repo_root() / "examples" / "tasks" / "coding" / "01-add-endpoint.json"
    )
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    return payload["output"]["learnings"][0]


def test_learning_schema_round_trips_through_json() -> None:
    learning = LearningSchemaModel.model_validate_json(json.dumps(_example_learning()))

    assert (
        LearningSchemaModel.model_validate_json(learning.model_dump_json()) == learning
    )


def test_learning_schema_emitter_writes_the_canonical_json_schema(
    tmp_path: Path,
) -> None:
    emitted_path = write_learning_schema(tmp_path / "learning.schema.json")

    assert (
        json.loads(emitted_path.read_text(encoding="utf-8")) == learning_schema_json()
    )


def test_checked_in_learning_schema_matches_the_emitted_model() -> None:
    checked_in_path = _repo_root() / "schemas" / "learning.schema.json"

    assert (
        json.loads(checked_in_path.read_text(encoding="utf-8"))
        == learning_schema_json()
    )


def test_learning_schema_validates_the_coding_task_example_instance() -> None:
    Draft202012Validator(learning_schema_json()).validate(_example_learning())


def test_learning_schema_rejects_unknown_enum_values() -> None:
    invalid = dict(_example_learning())
    invalid["scope"] = "workspace"

    with pytest.raises(ValidationError, match="scope"):
        LearningSchemaModel.model_validate_json(json.dumps(invalid))

    with pytest.raises(JsonSchemaValidationError, match="workspace"):
        Draft202012Validator(learning_schema_json()).validate(invalid)
