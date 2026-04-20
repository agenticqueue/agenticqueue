"""DoD handler for `artifact_size`."""

from __future__ import annotations

from agenticqueue_api.dod_checks.common import (
    DodCheckContext,
    DodCheckDefinition,
    DodCheckResult,
    DodCheckValidationError,
    DodItemState,
    optional_int,
    require_string,
    select_artifacts,
)


def run(
    definition: DodCheckDefinition,
    context: DodCheckContext,
) -> DodCheckResult:
    path_expr = require_string(definition, "path")
    min_bytes = optional_int(definition, "min_bytes", minimum=0)
    max_bytes = optional_int(definition, "max_bytes", minimum=0)
    if min_bytes is None and max_bytes is None:
        raise DodCheckValidationError(
            "DoD check 'artifact_size' requires 'min_bytes' and/or 'max_bytes'."
        )
    if min_bytes is not None and max_bytes is not None and min_bytes > max_bytes:
        raise DodCheckValidationError(
            "DoD check 'artifact_size' requires min_bytes <= max_bytes."
        )

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

    size = artifact.path.stat().st_size
    if min_bytes is not None and size < min_bytes:
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_UNMET,
            note=f"Artifact '{artifact.uri}' is {size} bytes, expected at least {min_bytes}.",
        )
    if max_bytes is not None and size > max_bytes:
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_UNMET,
            note=f"Artifact '{artifact.uri}' is {size} bytes, expected at most {max_bytes}.",
        )

    return DodCheckResult(
        item=definition.item,
        check_type=definition.check_type,
        state=DodItemState.CHECKED,
        note=f"Artifact '{artifact.uri}' size {size} bytes is within bounds.",
    )
