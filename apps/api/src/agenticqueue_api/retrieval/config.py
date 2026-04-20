"""Filesystem-backed retrieval config."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import os
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

DEFAULT_RETRIEVAL_CONFIG_PATH = (
    Path(__file__).resolve().parents[5] / "config" / "retrieval.yaml"
)
TRUE_ENV_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class VectorRerankConfig:
    """Cold-path rerank weights."""

    lexical_weight: float = 0.7
    recency_weight: float = 0.2
    access_count_weight: float = 0.1


@dataclass(frozen=True)
class RetrievalConfig:
    """Config values for tiered retrieval."""

    vector_candidate_limit: int = 50
    vector_project_scope_only: bool = True
    rerank: VectorRerankConfig = field(default_factory=VectorRerankConfig)


def get_retrieval_config_path() -> Path:
    """Return the retrieval config path."""

    configured = os.getenv("AGENTICQUEUE_RETRIEVAL_CONFIG") or os.getenv(
        "RETRIEVAL_CONFIG_PATH"
    )
    if configured:
        return Path(configured)
    return DEFAULT_RETRIEVAL_CONFIG_PATH


def _float_value(value: Any, *, default: float) -> float:
    if value is None:
        return default
    return float(value)


def _bool_value(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in TRUE_ENV_VALUES
    return bool(value)


def load_retrieval_config(path: Path) -> RetrievalConfig:
    """Load retrieval config from disk, falling back to defaults if absent."""

    if not path.exists():
        return RetrievalConfig()

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"Invalid retrieval config {path}: {error}") from error

    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("retrieval config must contain a YAML mapping")

    rerank_payload = payload.get("rerank", {})
    if rerank_payload is None:
        rerank_payload = {}
    if not isinstance(rerank_payload, dict):
        raise ValueError("retrieval config rerank block must be a mapping")

    vector_candidate_limit = int(
        payload.get("vector_candidate_limit", RetrievalConfig.vector_candidate_limit)
    )
    if vector_candidate_limit < 1:
        raise ValueError("vector_candidate_limit must be at least 1")

    return RetrievalConfig(
        vector_candidate_limit=vector_candidate_limit,
        vector_project_scope_only=_bool_value(
            payload.get(
                "vector_project_scope_only",
                RetrievalConfig.vector_project_scope_only,
            ),
            default=RetrievalConfig.vector_project_scope_only,
        ),
        rerank=VectorRerankConfig(
            lexical_weight=_float_value(
                rerank_payload.get(
                    "lexical_weight",
                    VectorRerankConfig.lexical_weight,
                ),
                default=VectorRerankConfig.lexical_weight,
            ),
            recency_weight=_float_value(
                rerank_payload.get(
                    "recency_weight",
                    VectorRerankConfig.recency_weight,
                ),
                default=VectorRerankConfig.recency_weight,
            ),
            access_count_weight=_float_value(
                rerank_payload.get(
                    "access_count_weight",
                    VectorRerankConfig.access_count_weight,
                ),
                default=VectorRerankConfig.access_count_weight,
            ),
        ),
    )


@lru_cache(maxsize=1)
def get_retrieval_config() -> RetrievalConfig:
    """Return the cached retrieval config."""

    return load_retrieval_config(get_retrieval_config_path())


__all__ = [
    "RetrievalConfig",
    "VectorRerankConfig",
    "get_retrieval_config",
    "get_retrieval_config_path",
    "load_retrieval_config",
]
