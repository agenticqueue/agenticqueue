"""Strict submission schemas."""

from agenticqueue_api.schemas.submit import (
    MAX_SUBMISSION_DEPTH,
    TaskCompletionSubmission,
    validate_task_completion_submission,
)

__all__ = [
    "MAX_SUBMISSION_DEPTH",
    "TaskCompletionSubmission",
    "validate_task_completion_submission",
]
