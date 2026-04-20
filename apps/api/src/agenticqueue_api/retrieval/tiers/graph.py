"""Graph-backed ranking tier."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from agenticqueue_api.learnings import rank_learnings_for_task
from agenticqueue_api.models.learning import LearningRecord
from agenticqueue_api.retrieval.types import RetrievalCandidate
from agenticqueue_api.schemas.learning import LearningStatus


def rank_candidates(
    session: Session,
    *,
    task_id,
    candidates: list[RetrievalCandidate],
) -> list[RetrievalCandidate]:
    """Rank candidates using the existing learning graph/scoring model."""

    if not candidates:
        return []

    active_count = session.scalar(
        sa.select(sa.func.count())
        .select_from(LearningRecord)
        .where(LearningRecord.status == LearningStatus.ACTIVE.value)
    )
    if not active_count:
        return list(candidates)

    candidate_by_id = {candidate.learning.id: candidate for candidate in candidates}
    ranked = rank_learnings_for_task(session, task_id, k=int(active_count))
    ordered_ids = [learning.id for learning in ranked if learning.id in candidate_by_id]
    ordered = [candidate_by_id[learning_id] for learning_id in ordered_ids]

    if len(ordered) == len(candidates):
        return ordered

    missing = [
        candidate
        for candidate in candidates
        if candidate.learning.id not in set(ordered_ids)
    ]
    missing.sort(
        key=lambda candidate: (
            candidate.learning.created_at,
            str(candidate.learning.id),
        ),
        reverse=True,
    )
    return [*ordered, *missing]


__all__ = ["rank_candidates"]
