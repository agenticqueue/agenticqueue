"""Shared retrieval types."""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
import uuid
from typing import Literal

from agenticqueue_api.models.learning import LearningModel, LearningRecord
from agenticqueue_api.models.task import TaskRecord

TierName = Literal[
    "surface_area",
    "graph",
    "metadata",
    "fts",
    "trgm",
    "vector",
    "rerank",
]


def _normalize_strings(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return tuple(normalized)


@dataclass(frozen=True)
class RetrievalScope:
    """Optional retrieval filters layered on top of one task."""

    project_id: uuid.UUID | None = None
    surface_area: tuple[str, ...] = ()
    owners: tuple[str, ...] = ()
    learning_types: tuple[str, ...] = ()
    max_age_days: int | None = None

    def normalized(self) -> RetrievalScope:
        """Return the scope with deduplicated string filters."""

        return RetrievalScope(
            project_id=self.project_id,
            surface_area=_normalize_strings(self.surface_area),
            owners=_normalize_strings(self.owners),
            learning_types=_normalize_strings(self.learning_types),
            max_age_days=self.max_age_days,
        )

    def with_defaults(self, task: TaskRecord) -> RetrievalScope:
        contract = task.contract or {}
        surface_area = self.surface_area
        if not surface_area:
            raw_surface_area = contract.get("surface_area")
            if isinstance(raw_surface_area, list):
                surface_area = tuple(
                    value.strip()
                    for value in raw_surface_area
                    if isinstance(value, str) and value.strip()
                )
        return RetrievalScope(
            project_id=self.project_id or task.project_id,
            surface_area=_normalize_strings(surface_area),
            owners=_normalize_strings(self.owners),
            learning_types=_normalize_strings(self.learning_types),
            max_age_days=self.max_age_days,
        )


@dataclass(frozen=True)
class RetrievalQuery:
    """Tiered retrieval query for one task."""

    task_id: uuid.UUID
    layers: tuple[str, ...] = ()
    scope: RetrievalScope = field(default_factory=RetrievalScope)
    k: int = 10
    fuzzy_global_search: bool = False

    def __post_init__(self) -> None:
        if self.k < 1:
            raise ValueError("k must be at least 1")
        object.__setattr__(self, "layers", _normalize_strings(self.layers))


@dataclass(frozen=True)
class RetrievalSearchQuery:
    """Tiered retrieval query driven by free-text search."""

    query: str
    layers: tuple[str, ...] = ()
    scope: RetrievalScope = field(default_factory=RetrievalScope)
    k: int = 10
    fuzzy_global_search: bool = True

    def __post_init__(self) -> None:
        query = self.query.strip()
        if not query:
            raise ValueError("query must not be empty")
        if self.k < 1:
            raise ValueError("k must be at least 1")
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "layers", _normalize_strings(self.layers))
        object.__setattr__(self, "scope", self.scope.normalized())


@dataclass(frozen=True)
class RetrievalCandidate:
    """One retrieved learning plus its source-task context."""

    learning: LearningRecord
    source_task: TaskRecord | None
    access_count: int = 0
    vector_similarity: float = 0.0

    @property
    def created_at(self) -> dt.datetime:
        return self.learning.created_at

    def to_model(self) -> LearningModel:
        return LearningModel.model_validate(self.learning)


@dataclass(frozen=True)
class RetrievalResult:
    """Tiered retrieval output."""

    items: list[LearningModel]
    tiers_fired: list[str]


__all__ = [
    "RetrievalCandidate",
    "RetrievalQuery",
    "RetrievalResult",
    "RetrievalSearchQuery",
    "RetrievalScope",
    "TierName",
]
