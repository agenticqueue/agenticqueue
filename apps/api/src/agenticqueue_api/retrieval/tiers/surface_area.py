"""Surface-area filtering tier."""

from __future__ import annotations

from agenticqueue_api.retrieval.types import RetrievalCandidate, RetrievalScope


def select_candidates(
    candidates: list[RetrievalCandidate],
    *,
    scope: RetrievalScope,
) -> list[RetrievalCandidate]:
    """Return candidates whose source task intersects the current surface area."""

    if not scope.surface_area:
        return list(candidates)

    wanted = set(scope.surface_area)
    selected: list[RetrievalCandidate] = []
    for candidate in candidates:
        if candidate.source_task is None:
            continue
        contract = candidate.source_task.contract or {}
        raw_surface_area = contract.get("surface_area")
        if not isinstance(raw_surface_area, list):
            continue
        candidate_surface_area = {
            value.strip()
            for value in raw_surface_area
            if isinstance(value, str) and value.strip()
        }
        if candidate_surface_area & wanted:
            selected.append(candidate)
    return selected


__all__ = ["select_candidates"]
