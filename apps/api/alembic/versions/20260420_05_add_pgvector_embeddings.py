"""Add pgvector embedding columns and indexes for retrieval entities."""

from __future__ import annotations

import op_ext

from alembic import op
import sqlalchemy as sa

from agenticqueue_api.pgvector import (
    EMBEDDING_COLUMN_NAME,
    EMBEDDING_TABLES,
    create_embedding_index_sql,
    drop_embedding_index_sql,
    embedding_vector_type,
)

revision = "20260420_05"
down_revision = "20260420_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in EMBEDDING_TABLES:
        op_ext.add_column_if_not_exists(
            table_name,
            sa.Column(
                EMBEDDING_COLUMN_NAME,
                embedding_vector_type(),
                nullable=True,
            ),
            schema="agenticqueue",
        )
        op.execute(create_embedding_index_sql(table_name))


def downgrade() -> None:
    for table_name in reversed(EMBEDDING_TABLES):
        op.execute(drop_embedding_index_sql(table_name))
        op_ext.drop_column_if_exists(table_name, EMBEDDING_COLUMN_NAME, schema="agenticqueue")
