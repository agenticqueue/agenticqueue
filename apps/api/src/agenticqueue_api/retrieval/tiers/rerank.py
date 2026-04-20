"""Cold-path rerank tier."""

from __future__ import annotations

import datetime as dt
import re

from agenticqueue_api.models.task import TaskRecord
from agenticqueue_api.retrieval.config import RetrievalConfig
from agenticqueue_api.retrieval.tiers.vector import (
    learning_similarity_text,
    task_similarity_text,
)
from agenticqueue_api.retrieval.types import RetrievalCandidate

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(text.lower()) if len(token) > 1}


def _jaccard(lhs: set[str], rhs: set[str]) -> float:
    if not lhs or not rhs:
        return 0.0
    union = lhs | rhs
    if not union:
        return 0.0
    return len(lhs & rhs) / len(union)


def _recency_score(created_at: dt.datetime, *, reference: dt.datetime) -> float:
    age_days = max((reference - created_at).total_seconds(), 0.0) / 86400.0
    return max(0.0, 1.0 - min(age_days, 30.0) / 30.0)


def rerank_candidates(
    *,
    task: TaskRecord,
    candidates: list[RetrievalCandidate],
    config: RetrievalConfig,
    limit: int,
) -> list[RetrievalCandidate]:
    """Rerank vector-augmented candidates with lexical + recency + access."""

    if not candidates or limit < 1:
        return []

    task_tokens = _tokens(task_similarity_text(task))
    max_access_count = max(
        (candidate.access_count for candidate in candidates), default=0
    )

    scored: list[tuple[float, RetrievalCandidate]] = []
    for candidate in candidates:
        lexical_score = _jaccard(
            task_tokens, _tokens(learning_similarity_text(candidate))
        )
        recency_score = _recency_score(
            candidate.learning.created_at, reference=task.created_at
        )
        access_score = (
            candidate.access_count / max_access_count if max_access_count > 0 else 0.0
        )
        score = (
            lexical_score * config.rerank.lexical_weight
            + recency_score * config.rerank.recency_weight
            + access_score * config.rerank.access_count_weight
        )
        scored.append((score, candidate))

    scored.sort(
        key=lambda item: (
            -item[0],
            -item[1].vector_similarity,
            item[1].learning.created_at,
            str(item[1].learning.id),
        ),
    )
    return [candidate for _, candidate in scored[:limit]]


__all__ = ["rerank_candidates"]
