"""Declarative DoD check handlers."""

from __future__ import annotations

from collections.abc import Callable

from agenticqueue_api.dod_checks import (
    artifact_size,
    ci_status,
    grep_absent,
    grep_present,
    path_absent,
    path_exists,
    pr_mergeable,
    schema_validates,
    test_count,
)
from agenticqueue_api.dod_checks.common import DodCheckContext, DodCheckDefinition, DodCheckResult

CheckHandler = Callable[[DodCheckDefinition, DodCheckContext], DodCheckResult]

CHECK_HANDLERS: dict[str, CheckHandler] = {
    "artifact_size": artifact_size.run,
    "ci_status": ci_status.run,
    "grep_absent": grep_absent.run,
    "grep_present": grep_present.run,
    "path_absent": path_absent.run,
    "path_exists": path_exists.run,
    "pr_mergeable": pr_mergeable.run,
    "schema_validates": schema_validates.run,
    "test_count": test_count.run,
}

VALID_CHECK_TYPES = tuple(sorted(CHECK_HANDLERS))

__all__ = ["CHECK_HANDLERS", "VALID_CHECK_TYPES", "CheckHandler"]
