"""DoD handler for `test_count`."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from agenticqueue_api.dod_checks.common import (
    DodCheckContext,
    DodCheckDefinition,
    DodCheckResult,
    DodItemState,
    require_int,
    require_string,
    select_artifacts,
)


def run(
    definition: DodCheckDefinition,
    context: DodCheckContext,
) -> DodCheckResult:
    path_expr = require_string(definition, "path")
    minimum = require_int(definition, "min_count", minimum=0)
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
        root = ET.fromstring(artifact.path.read_text(encoding="utf-8"))
    except (OSError, ET.ParseError) as error:
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_UNMET,
            note=f"Artifact '{artifact.uri}' is not valid JUnit XML: {error}",
        )

    count = len(root.findall(".//testcase"))
    if count < minimum:
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_UNMET,
            note=f"Found {count} testcases in '{artifact.uri}', expected at least {minimum}.",
        )

    return DodCheckResult(
        item=definition.item,
        check_type=definition.check_type,
        state=DodItemState.CHECKED,
        note=f"Found {count} testcases in '{artifact.uri}'.",
    )
