"""Strict submission schemas."""

from agenticqueue_api.schemas.learning import (
    LearningConfidence,
    LearningSchemaModel,
    LearningScope,
    LearningStatus,
    LearningType,
    default_learning_schema_path,
    learning_schema_json,
    write_learning_schema,
)
from agenticqueue_api.schemas.submit import (
    MAX_SUBMISSION_DEPTH,
    TaskCompletionSubmission,
    validate_task_completion_submission,
)

__all__ = [
    "LearningConfidence",
    "LearningSchemaModel",
    "LearningScope",
    "LearningStatus",
    "LearningType",
    "MAX_SUBMISSION_DEPTH",
    "TaskCompletionSubmission",
    "default_learning_schema_path",
    "learning_schema_json",
    "validate_task_completion_submission",
    "write_learning_schema",
]
