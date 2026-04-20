"""DoD handler for `path_exists`."""

from __future__ import annotations

from agenticqueue_api.dod_checks.common import (
    DodCheckContext,
    DodCheckDefinition,
    DodCheckResult,
    DodItemState,
    optional_string,
    require_string,
    select_artifacts,
)


def run(
    definition: DodCheckDefinition,
    context: DodCheckContext,
) -> DodCheckResult:
    path_expr = require_string(definition, "path")
    path_mode = optional_string(definition, "path_mode", default="exact") or "exact"
    artifacts = select_artifacts(context.bundle, path_expr=path_expr, path_mode=path_mode)
    if not artifacts:
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_UNMET,
            note=f"No declared artifact matched '{path_expr}'.",
        )

    missing = [artifact.uri for artifact in artifacts if not artifact.path.exists()]
    if missing:
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_UNMET,
            note="Missing artifact files: " + ", ".join(sorted(missing)),
        )

    return DodCheckResult(
        item=definition.item,
        check_type=definition.check_type,
        state=DodItemState.CHECKED,
        note="Matched artifact(s): " + ", ".join(sorted(artifact.uri for artifact in artifacts)),
    )
