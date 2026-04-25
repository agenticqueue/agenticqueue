"""Add the memory_item table for Phase 4 memory layers."""

from __future__ import annotations

import op_ext

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from agenticqueue_api.memory.layers import MEMORY_LAYER_ENUM
from agenticqueue_api.pgvector import embedding_vector_type

revision = "20260420_17"
down_revision = "20260420_16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op_ext.create_table_if_not_exists(
        "memory_item",
        sa.Column("layer", MEMORY_LAYER_ENUM, nullable=False),
        sa.Column("scope_id", sa.UUID(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("embedding", embedding_vector_type(), nullable=True),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column(
            "surface_area",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "access_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_item")),
        sa.UniqueConstraint(
            "layer",
            "scope_id",
            "content_hash",
            name="uq_memory_item_layer_scope_id_content_hash",
        ),
        schema="agenticqueue",
    )
    op_ext.create_index_if_not_exists(
        "ix_memory_item_surface_area_gin",
        "memory_item",
        ["surface_area"],
        unique=False,
        schema="agenticqueue",
        postgresql_using="gin",
    )


def downgrade() -> None:
    op_ext.drop_index_if_exists(
        "ix_memory_item_surface_area_gin",
        table_name="memory_item",
        schema="agenticqueue",
    )
    op_ext.drop_table_if_exists("memory_item", schema="agenticqueue")
