"""Vector fallback tier."""

from __future__ import annotations

from dataclasses import replace
import uuid

from agenticqueue_api.learnings.dedupe import LearningDedupeService, cosine_similarity
from agenticqueue_api.models.task import TaskRecord
from agenticqueue_api.retrieval.types import RetrievalCandidate


def _normalize_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def task_similarity_text(task: TaskRecord) -> str:
    """Return the canonical task text used for cold-path similarity."""

    contract = task.contract or {}
    parts = [
        task.task_type,
        task.title,
        task.description or "",
        *(_normalize_string_list(contract.get("file_scope"))),
        *(_normalize_string_list(contract.get("surface_area"))),
    ]
    spec = contract.get("spec")
    if isinstance(spec, str) and spec.strip():
        parts.append(spec.strip())
    return "\n".join(part for part in parts if part)


def learning_similarity_text(candidate: RetrievalCandidate) -> str:
    """Return the canonical learning text used for cold-path similarity."""

    learning = candidate.learning
    parts = [
        learning.title,
        learning.action_rule,
        learning.what_happened,
        learning.what_learned,
        *learning.evidence,
    ]
    if candidate.source_task is not None:
        contract = candidate.source_task.contract or {}
        parts.extend(_normalize_string_list(contract.get("file_scope")))
        parts.extend(_normalize_string_list(contract.get("surface_area")))
        spec = contract.get("spec")
        if isinstance(spec, str) and spec.strip():
            parts.append(spec.strip())
    return "\n".join(part for part in parts if part)


def vector_candidates(
    session,
    *,
    task: TaskRecord,
    candidates: list[RetrievalCandidate],
    exclude_ids: set[uuid.UUID],
    limit: int,
) -> list[RetrievalCandidate]:
    """Return the top-N vector candidates for one task."""

    if limit < 1:
        return []

    dedupe = LearningDedupeService(session)
    task_embedding = dedupe.embed_text(task_similarity_text(task))
    matches: list[tuple[float, RetrievalCandidate]] = []
    for candidate in candidates:
        if candidate.learning.id in exclude_ids:
            continue
        similarity = cosine_similarity(
            task_embedding,
            dedupe.embed_text(learning_similarity_text(candidate)),
        )
        if similarity <= 0.0:
            continue
        matches.append((similarity, replace(candidate, vector_similarity=similarity)))

    matches.sort(
        key=lambda item: (
            -item[0],
            item[1].learning.created_at,
            str(item[1].learning.id),
        ),
    )
    return [candidate for _, candidate in matches[:limit]]


def vector_text_candidates(
    session,
    *,
    query_text: str,
    candidates: list[RetrievalCandidate],
    exclude_ids: set[uuid.UUID],
    limit: int,
) -> list[RetrievalCandidate]:
    """Return the top-N vector candidates for one free-text query."""

    if limit < 1:
        return []

    dedupe = LearningDedupeService(session)
    query_embedding = dedupe.embed_text(query_text)
    matches: list[tuple[float, RetrievalCandidate]] = []
    for candidate in candidates:
        if candidate.learning.id in exclude_ids:
            continue
        similarity = cosine_similarity(
            query_embedding,
            dedupe.embed_text(learning_similarity_text(candidate)),
        )
        if similarity <= 0.0:
            continue
        matches.append((similarity, replace(candidate, vector_similarity=similarity)))

    matches.sort(
        key=lambda item: (
            -item[0],
            item[1].learning.created_at,
            str(item[1].learning.id),
        ),
    )
    return [candidate for _, candidate in matches[:limit]]


__all__ = [
    "learning_similarity_text",
    "task_similarity_text",
    "vector_candidates",
    "vector_text_candidates",
]
