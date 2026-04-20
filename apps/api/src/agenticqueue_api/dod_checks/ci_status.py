"""DoD handler for `ci_status`."""

from __future__ import annotations

from agenticqueue_api.dod_checks.common import (
    DodCheckContext,
    DodCheckDefinition,
    DodCheckResult,
    DodItemState,
    require_string,
)


def run(
    definition: DodCheckDefinition,
    context: DodCheckContext,
) -> DodCheckResult:
    repo = require_string(definition, "repo")
    sha = require_string(definition, "sha")
    check_name = require_string(definition, "check_name")
    if context.github_client is None:
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_BLOCKED,
            note="No GitHub client configured for ci_status checks.",
        )

    try:
        conclusion = context.github_client.get_check_conclusion(
            repo=repo,
            sha=sha,
            check_name=check_name,
            timeout_seconds=definition.timeout_seconds,
        )
    except Exception as error:  # pragma: no cover - exercised via tests
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_BLOCKED,
            note=f"GitHub check lookup failed: {error}",
        )

    if conclusion is None:
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_BLOCKED,
            note=f"Check '{check_name}' on {sha} is not available yet.",
        )

    if conclusion != "success":
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_UNMET,
            note=f"Check '{check_name}' concluded '{conclusion}'.",
        )

    return DodCheckResult(
        item=definition.item,
        check_type=definition.check_type,
        state=DodItemState.CHECKED,
        note=f"Check '{check_name}' succeeded on {sha}.",
    )
