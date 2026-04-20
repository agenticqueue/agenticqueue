"""Shared pgvector helpers for AgenticQueue entity embeddings."""

from __future__ import annotations

from typing import Any, Final

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy.orm import mapped_column

from agenticqueue_api.config import get_embedding_dimension, get_vector_ivfflat_lists

EMBEDDING_COLUMN_NAME: Final = "embedding"
EMBEDDING_INDEX_OPCLASS: Final = "vector_cosine_ops"
EMBEDDING_SCHEMA: Final = "agenticqueue"
EMBEDDING_TABLES: Final[tuple[str, ...]] = ("artifact", "decision", "learning")


def embedding_vector_type() -> Vector:
    """Return the configured pgvector type for embeddings."""
    return Vector(get_embedding_dimension())


def embedding_column() -> Any:
    """Return a nullable embedding column definition."""
    return mapped_column(embedding_vector_type(), nullable=True)


def normalize_embedding(value: Any) -> list[float] | None:
    """Coerce pgvector values into plain Python lists for schemas and JSON."""
    if value is None:
        return None
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list):
        if all(isinstance(item, (int, float)) for item in value):
            return [float(item) for item in value]
        return value
    return value


def embedding_index_name(table_name: str) -> str:
    """Return the canonical ivfflat index name for an embedding column."""
    return f"ix_{table_name}_{EMBEDDING_COLUMN_NAME}_ivfflat"


def create_embedding_index_sql(table_name: str) -> str:
    """Return the ivfflat CREATE INDEX statement for a table's embedding column."""
    return (
        f"CREATE INDEX {embedding_index_name(table_name)} "
        f"ON {EMBEDDING_SCHEMA}.{table_name} USING ivfflat "
        f"({EMBEDDING_COLUMN_NAME} {EMBEDDING_INDEX_OPCLASS}) "
        f"WITH (lists = {get_vector_ivfflat_lists()}) "
        f"WHERE {EMBEDDING_COLUMN_NAME} IS NOT NULL"
    )


def drop_embedding_index_sql(table_name: str) -> str:
    """Return the DROP INDEX statement for a table's embedding index."""
    return f"DROP INDEX IF EXISTS {EMBEDDING_SCHEMA}.{embedding_index_name(table_name)}"
