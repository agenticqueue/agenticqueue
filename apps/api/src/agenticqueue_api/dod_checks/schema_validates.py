"""DoD handler for `schema_validates`."""

from __future__ import annotations

import json

from jsonschema import ValidationError as JsonSchemaValidationError  # type: ignore[import-untyped]
from jsonschema.validators import Draft202012Validator  # type: ignore[import-untyped]

from agenticqueue_api.dod_checks.common import (
    DodCheckContext,
    DodCheckDefinition,
    DodCheckResult,
    DodCheckValidationError,
    DodItemState,
    require_string,
    select_artifacts,
)
from agenticqueue_api.task_type_registry import SchemaLoadError


def run(
    definition: DodCheckDefinition,
    context: DodCheckContext,
) -> DodCheckResult:
    path_expr = require_string(definition, "path")
    schema_name = require_string(definition, "schema_name")
    artifacts = select_artifacts(context.bundle, path_expr=path_expr)
    if not artifacts:
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_UNMET,
            note=f"No declared artifact matched '{path_expr}'.",
        )

    artifact = artifacts[0]
    if not artifact.path.exists():
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_UNMET,
            note=f"Artifact '{artifact.uri}' does not exist.",
        )

    try:
        document = json.loads(artifact.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_UNMET,
            note=f"Artifact '{artifact.uri}' is not valid JSON: {error}",
        )

    try:
        schema = context.registry.get(schema_name).schema
    except SchemaLoadError as error:
        raise DodCheckValidationError(str(error)) from error

    try:
        Draft202012Validator(schema).validate(document)
    except JsonSchemaValidationError as error:
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_UNMET,
            note=f"Schema validation failed: {error.message}",
        )

    return DodCheckResult(
        item=definition.item,
        check_type=definition.check_type,
        state=DodItemState.CHECKED,
        note=f"Artifact '{artifact.uri}' matches schema '{schema_name}'.",
    )
