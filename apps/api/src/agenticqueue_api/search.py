"""Postgres full-text and trigram search helpers for retrieval entities."""

from __future__ import annotations

from typing import Final

SEARCH_SCHEMA: Final = "agenticqueue"
SEARCH_CONFIG: Final = "english"
SEARCH_TEXT_COLUMN_NAME: Final = "search_text"
SEARCH_DOCUMENT_COLUMN_NAME: Final = "search_document"
SEARCH_TABLES: Final[tuple[str, ...]] = ("artifact", "decision", "learning")
TRIGRAM_SOURCE_COLUMNS: Final[dict[str, str]] = {
    "artifact": "uri",
    "decision": "summary",
    "learning": "title",
}

SEARCH_TEXT_EXPRESSIONS: Final[dict[str, str]] = {
    "artifact": (
        "coalesce(kind, '') || ' ' || coalesce(uri, '') || ' ' || "
        "coalesce(details::text, '')"
    ),
    "decision": "coalesce(summary, '') || ' ' || coalesce(rationale, '')",
    "learning": (
        "coalesce(title, '') || ' ' || coalesce(what_happened, '') || ' ' || "
        "coalesce(what_learned, '') || ' ' || coalesce(action_rule, '') || ' ' || "
        "coalesce(applies_when, '') || ' ' || coalesce(does_not_apply_when, '')"
    ),
}


def search_text_expression(table_name: str) -> str:
    """Return the computed text expression for one searchable table."""

    try:
        return SEARCH_TEXT_EXPRESSIONS[table_name]
    except KeyError as error:
        raise ValueError(f"unsupported search table: {table_name}") from error


def search_document_expression(table_name: str) -> str:
    """Return the computed TSVECTOR expression for one searchable table."""

    return f"to_tsvector('{SEARCH_CONFIG}', {search_text_expression(table_name)})"


def search_document_index_name(table_name: str) -> str:
    """Return the canonical GIN index name for a search document column."""

    return f"ix_{table_name}_{SEARCH_DOCUMENT_COLUMN_NAME}_gin"


def search_text_trgm_index_name(table_name: str) -> str:
    """Return the canonical trigram index name for one table."""

    return f"ix_{table_name}_{search_trigram_column_name(table_name)}_trgm"


def search_trigram_column_name(table_name: str) -> str:
    """Return the primary text column used for trigram similarity."""

    try:
        return TRIGRAM_SOURCE_COLUMNS[table_name]
    except KeyError as error:
        raise ValueError(f"unsupported trigram search table: {table_name}") from error


__all__ = [
    "SEARCH_CONFIG",
    "SEARCH_DOCUMENT_COLUMN_NAME",
    "SEARCH_SCHEMA",
    "SEARCH_TABLES",
    "SEARCH_TEXT_COLUMN_NAME",
    "search_document_expression",
    "search_document_index_name",
    "search_text_expression",
    "search_text_trgm_index_name",
    "search_trigram_column_name",
]
