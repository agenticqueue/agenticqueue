"""Create the Phase 1 edge table."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260419_02"
down_revision = "4d68ebda8f16"
branch_labels = None
depends_on = None

EDGE_RELATION_ENUM = sa.Enum(
    "depends_on",
    "blocks",
    "unblocks",
    "parallel",
    "gated_by",
    "supersedes",
    "informed_by",
    "implements",
    "produced",
    "reviewed_by",
    "validated_by",
    "triggered",
    "contradicts",
    "derived_from",
    "requires_approval",
    "learned_from",
    "parent_of",
    name="edge_relation",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "edge",
        sa.Column("src_entity_type", sa.String(length=64), nullable=False),
        sa.Column("src_id", sa.UUID(), nullable=False),
        sa.Column("dst_entity_type", sa.String(length=64), nullable=False),
        sa.Column("dst_id", sa.UUID(), nullable=False),
        sa.Column("relation", EDGE_RELATION_ENUM, nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["agenticqueue.actor.id"],
            name=op.f("fk_edge_created_by_actor"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_edge")),
        sa.UniqueConstraint(
            "src_entity_type",
            "src_id",
            "dst_entity_type",
            "dst_id",
            "relation",
            name="uq_edge_signature",
        ),
        schema="agenticqueue",
    )
    op.create_index(
        "ix_edge_src_lookup",
        "edge",
        ["src_entity_type", "src_id", "relation"],
        unique=False,
        schema="agenticqueue",
    )
    op.create_index(
        "ix_edge_dst_lookup",
        "edge",
        ["dst_entity_type", "dst_id", "relation"],
        unique=False,
        schema="agenticqueue",
    )


def downgrade() -> None:
    op.drop_index("ix_edge_dst_lookup", table_name="edge", schema="agenticqueue")
    op.drop_index("ix_edge_src_lookup", table_name="edge", schema="agenticqueue")
    op.drop_table("edge", schema="agenticqueue")
