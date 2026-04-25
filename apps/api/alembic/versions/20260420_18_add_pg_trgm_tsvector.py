"""Add pg_trgm search columns and indexes for retrieval entities."""

from __future__ import annotations

import op_ext

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from agenticqueue_api.search import (
    SEARCH_DOCUMENT_COLUMN_NAME,
    SEARCH_TABLES,
    SEARCH_TEXT_COLUMN_NAME,
    search_document_expression,
    search_document_index_name,
    search_text_expression,
    search_text_trgm_index_name,
    search_trigram_column_name,
)

revision = "20260420_18"
down_revision = "20260420_17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    for table_name in SEARCH_TABLES:
        op_ext.add_column_if_not_exists(
            table_name,
            sa.Column(
                SEARCH_TEXT_COLUMN_NAME,
                sa.Text(),
                sa.Computed(search_text_expression(table_name), persisted=True),
                nullable=False,
            ),
            schema="agenticqueue",
        )
        op_ext.add_column_if_not_exists(
            table_name,
            sa.Column(
                SEARCH_DOCUMENT_COLUMN_NAME,
                postgresql.TSVECTOR(),
                sa.Computed(search_document_expression(table_name), persisted=True),
                nullable=False,
            ),
            schema="agenticqueue",
        )
        op_ext.create_index_if_not_exists(
            search_document_index_name(table_name),
            table_name,
            [SEARCH_DOCUMENT_COLUMN_NAME],
            unique=False,
            schema="agenticqueue",
            postgresql_using="gin",
        )
        op_ext.create_index_if_not_exists(
            search_text_trgm_index_name(table_name),
            table_name,
            [search_trigram_column_name(table_name)],
            unique=False,
            schema="agenticqueue",
            postgresql_using="gin",
            postgresql_ops={search_trigram_column_name(table_name): "gin_trgm_ops"},
        )


def downgrade() -> None:
    for table_name in reversed(SEARCH_TABLES):
        op.execute(
            f"DROP INDEX IF EXISTS agenticqueue.{search_text_trgm_index_name(table_name)}"
        )
        op.execute(
            f"DROP INDEX IF EXISTS agenticqueue.ix_{table_name}_{SEARCH_TEXT_COLUMN_NAME}_trgm"
        )
        op_ext.drop_index_if_exists(
            search_document_index_name(table_name),
            table_name=table_name,
            schema="agenticqueue",
        )
        op_ext.drop_column_if_exists(
            table_name,
            SEARCH_DOCUMENT_COLUMN_NAME,
            schema="agenticqueue",
        )
        op_ext.drop_column_if_exists(
            table_name,
            SEARCH_TEXT_COLUMN_NAME,
            schema="agenticqueue",
        )
