"""Metadata filtering tier."""

from __future__ import annotations

import datetime as dt

from agenticqueue_api.retrieval.types import RetrievalCandidate


def apply_filters(
    candidates: list[RetrievalCandidate],
    *,
    layers: tuple[str, ...],
    owners: tuple[str, ...],
    learning_types: tuple[str, ...],
    reference: dt.datetime,
    max_age_days: int | None,
) -> list[RetrievalCandidate]:
    """Apply metadata filters without disturbing graph rank order."""

    filtered: list[RetrievalCandidate] = []
    for candidate in candidates:
        learning = candidate.learning
        if layers and learning.scope not in layers:
            continue
        if owners and (learning.owner or "") not in owners:
            continue
        if learning_types and learning.learning_type not in learning_types:
            continue
        if max_age_days is not None:
            age_days = (reference - learning.created_at).total_seconds() / 86400.0
            if age_days > float(max_age_days):
                continue
        filtered.append(candidate)
    return filtered


__all__ = ["apply_filters"]
