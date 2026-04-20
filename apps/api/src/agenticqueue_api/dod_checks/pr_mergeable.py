"""DoD handler for `pr_mergeable`."""

from __future__ import annotations

from agenticqueue_api.dod_checks.common import (
    DodCheckContext,
    DodCheckDefinition,
    DodCheckResult,
    DodItemState,
    require_int,
    require_string,
)


def run(
    definition: DodCheckDefinition,
    context: DodCheckContext,
) -> DodCheckResult:
    repo = require_string(definition, "repo")
    pr_number = require_int(definition, "pr_number", minimum=1)
    if context.github_client is None:
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_BLOCKED,
            note="No GitHub client configured for pr_mergeable checks.",
        )

    try:
        mergeable = context.github_client.get_pull_request_mergeable(
            repo=repo,
            pr_number=pr_number,
            timeout_seconds=definition.timeout_seconds,
        )
    except Exception as error:  # pragma: no cover - exercised via tests
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_BLOCKED,
            note=f"GitHub PR lookup failed: {error}",
        )

    if mergeable is None:
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_BLOCKED,
            note=f"PR #{pr_number} mergeability is not available yet.",
        )

    if not mergeable:
        return DodCheckResult(
            item=definition.item,
            check_type=definition.check_type,
            state=DodItemState.UNCHECKED_UNMET,
            note=f"PR #{pr_number} is not mergeable.",
        )

    return DodCheckResult(
        item=definition.item,
        check_type=definition.check_type,
        state=DodItemState.CHECKED,
        note=f"PR #{pr_number} is mergeable.",
    )
