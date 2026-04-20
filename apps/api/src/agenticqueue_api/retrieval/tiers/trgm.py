"""Trigram similarity fallback tier."""

from __future__ import annotations

from dataclasses import replace
import re
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from agenticqueue_api.models.learning import LearningRecord
from agenticqueue_api.retrieval.types import RetrievalCandidate
from agenticqueue_api.search import search_trigram_column_name

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_query_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip()


def trgm_candidates(
    session: Session,
    *,
    query_text: str,
    candidates: list[RetrievalCandidate],
    exclude_ids: set[uuid.UUID],
    limit: int,
) -> list[RetrievalCandidate]:
    """Return fuzzy matches using the primary trigram-indexed text column."""

    if limit < 1:
        return []

    normalized_query = _normalize_query_text(query_text)
    if not normalized_query:
        return []

    candidate_map = {
        candidate.learning.id: candidate
        for candidate in candidates
        if candidate.learning.id not in exclude_ids
    }
    if not candidate_map:
        return []

    trigram_column = getattr(
        LearningRecord,
        search_trigram_column_name(LearningRecord.__tablename__),
    )
    similarity = sa.func.similarity(trigram_column, normalized_query).label(
        "trgm_similarity"
    )
    rows = session.execute(
        sa.select(LearningRecord.id, similarity)
        .where(LearningRecord.id.in_(list(candidate_map)))
        .where(trigram_column.op("%")(normalized_query))
        .order_by(
            sa.desc(similarity),
            LearningRecord.created_at.asc(),
            LearningRecord.id.asc(),
        )
        .limit(limit)
    )

    matches: list[RetrievalCandidate] = []
    for learning_id, trgm_similarity in rows:
        candidate = candidate_map.get(learning_id)
        if candidate is None:
            continue
        matches.append(
            replace(
                candidate,
                vector_similarity=float(trgm_similarity or 0.0),
            )
        )
    return matches


__all__ = ["trgm_candidates"]
