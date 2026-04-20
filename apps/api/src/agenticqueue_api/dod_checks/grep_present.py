"""DoD handler for `grep_present`."""

from __future__ import annotations

import re

from agenticqueue_api.dod_checks.common import (
    DodCheckContext,
    DodCheckDefinition,
    DodCheckResult,
    DodCheckValidationError,
    DodItemState,
    read_text,
    require_string,
    select_artifacts,
)


def run(
    definition: DodCheckDefinition,
    context: DodCheckContext,
) -> DodCheckResult:
    path_expr = require_string(definition, "path")
    pattern = require_string(definition, "pattern")
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
        matcher = re.compile(pattern, re.MULTILINE)
    except re.error as error:
        raise DodCheckValidationError(
            f"Invalid grep regex '{pattern}': {error.msg}"
        ) from error

    text = read_text(artifact)
    if matcher.search(text) is None:
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_UNMET,
            note=f"Pattern '{pattern}' not found in '{artifact.uri}'.",
        )

    return DodCheckResult(
        item=definition.item,
        check_type=definition.check_type,
        state=DodItemState.CHECKED,
        note=f"Pattern '{pattern}' found in '{artifact.uri}'.",
    )
