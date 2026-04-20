"""Full-text search fallback tier."""

from __future__ import annotations

from dataclasses import replace
import re
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from agenticqueue_api.models.learning import LearningRecord
from agenticqueue_api.retrieval.types import RetrievalCandidate
from agenticqueue_api.search import SEARCH_CONFIG

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _to_tsquery(value: str) -> str:
    tokens: list[str] = []
    seen: set[str] = set()
    for raw_token in _TOKEN_RE.findall(value.lower()):
        if len(raw_token) < 2 or raw_token in seen:
            continue
        seen.add(raw_token)
        tokens.append(raw_token)
    return " | ".join(tokens)


def fts_candidates(
    session: Session,
    *,
    query_text: str,
    candidates: list[RetrievalCandidate],
    exclude_ids: set[uuid.UUID],
    limit: int,
) -> list[RetrievalCandidate]:
    """Return lexical matches from the generated TSVECTOR column."""

    if limit < 1:
        return []

    candidate_map = {
        candidate.learning.id: candidate
        for candidate in candidates
        if candidate.learning.id not in exclude_ids
    }
    if not candidate_map:
        return []

    tsquery = _to_tsquery(query_text)
    if not tsquery:
        return []

    tsquery_expr = sa.func.to_tsquery(SEARCH_CONFIG, tsquery)
    rank = sa.func.ts_rank_cd(LearningRecord.search_document, tsquery_expr).label(
        "fts_rank"
    )
    rows = session.execute(
        sa.select(LearningRecord.id, rank)
        .where(LearningRecord.id.in_(list(candidate_map)))
        .where(LearningRecord.search_document.op("@@")(tsquery_expr))
        .order_by(
            sa.desc(rank), LearningRecord.created_at.asc(), LearningRecord.id.asc()
        )
        .limit(limit)
    )

    matches: list[RetrievalCandidate] = []
    for learning_id, fts_rank in rows:
        candidate = candidate_map.get(learning_id)
        if candidate is None:
            continue
        matches.append(
            replace(
                candidate,
                vector_similarity=float(fts_rank or 0.0),
            )
        )
    return matches


__all__ = ["fts_candidates"]
