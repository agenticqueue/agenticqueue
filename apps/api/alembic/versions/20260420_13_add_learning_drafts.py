"""Add persisted learning drafts plus the learning owner field."""

from __future__ import annotations

import op_ext

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260420_13"
down_revision = "20260420_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op_ext.add_column_if_not_exists(
        "learning",
        sa.Column("owner", sa.String(length=255), nullable=True),
        schema="agenticqueue",
    )
    op_ext.create_table_if_not_exists(
        "learning_drafts",
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "draft_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column(
            "confirmed_learning_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_learning_id"],
            ["agenticqueue.learning.id"],
            name=op.f("fk_learning_drafts_confirmed_learning_id_learning"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agenticqueue.run.id"],
            name=op.f("fk_learning_drafts_run_id_run"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["agenticqueue.task.id"],
            name=op.f("fk_learning_drafts_task_id_task"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_drafts")),
        schema="agenticqueue",
    )
    op_ext.create_index_if_not_exists(
        "ix_learning_drafts_task_id",
        "learning_drafts",
        ["task_id"],
        unique=False,
        schema="agenticqueue",
    )
    op_ext.create_index_if_not_exists(
        "ix_learning_drafts_run_id",
        "learning_drafts",
        ["run_id"],
        unique=False,
        schema="agenticqueue",
    )
    op_ext.create_index_if_not_exists(
        "ix_learning_drafts_draft_status",
        "learning_drafts",
        ["draft_status"],
        unique=False,
        schema="agenticqueue",
    )


def downgrade() -> None:
    op_ext.drop_index_if_exists(
        "ix_learning_drafts_draft_status",
        table_name="learning_drafts",
        schema="agenticqueue",
    )
    op_ext.drop_index_if_exists(
        "ix_learning_drafts_run_id",
        table_name="learning_drafts",
        schema="agenticqueue",
    )
    op_ext.drop_index_if_exists(
        "ix_learning_drafts_task_id",
        table_name="learning_drafts",
        schema="agenticqueue",
    )
    op_ext.drop_table_if_exists("learning_drafts", schema="agenticqueue")
    op_ext.drop_column_if_exists("learning", "owner", schema="agenticqueue")
