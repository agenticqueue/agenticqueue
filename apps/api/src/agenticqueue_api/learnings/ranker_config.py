"""Config values for learning relevance ranking."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LearningRankerConfig:
    """Tunables for learning relevance ranking and diversity."""

    task_type_match_weight: float = 1.0
    project_match_weight: float = 1.6
    repo_scope_overlap_weight: float = 1.5
    linked_decision_overlap_weight: float = 1.25
    shared_dependency_overlap_weight: float = 1.0
    tooling_overlap_weight: float = 0.8
    text_similarity_weight: float = 2.2
    recency_weight: float = 0.2
    task_scope_bonus: float = 0.25
    project_scope_bonus: float = 0.15
    global_scope_bonus: float = 0.15
    diversity_similarity_threshold: float = 0.94


DEFAULT_LEARNING_RANKER_CONFIG = LearningRankerConfig()


__all__ = [
    "DEFAULT_LEARNING_RANKER_CONFIG",
    "LearningRankerConfig",
]
