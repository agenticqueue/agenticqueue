"""Stub packet compiler for learnings-loop tests.

Phase 3's real packet compiler is not landed yet. This helper provides the
minimal packet shape Phase 2.5 needs so the learnings loop can be exercised
end to end in tests.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from agenticqueue_api.learnings import rank_learnings_for_task
from agenticqueue_api.models.task import TaskRecord


def compile_packet(
    session: Session,
    task_id: uuid.UUID,
    *,
    limit: int = 5,
) -> dict[str, Any]:
    """Return a minimal packet with the target task and ranked learnings."""

    task = session.get(TaskRecord, task_id)
    if task is None:
        raise KeyError(str(task_id))

    return {
        "task": {
            "id": str(task.id),
            "project_id": str(task.project_id),
            "task_type": task.task_type,
            "title": task.title,
            "state": task.state,
        },
        "relevant_learnings": [
            learning.model_dump(mode="json")
            for learning in rank_learnings_for_task(session, task.id, k=limit)
        ],
    }

