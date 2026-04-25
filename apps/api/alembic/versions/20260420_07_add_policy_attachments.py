"""Add policy capabilities plus workspace/project/task policy attachments."""

from __future__ import annotations

import op_ext

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260420_07"
down_revision = "20260420_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op_ext.add_column_if_not_exists(
        "policy",
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        schema="agenticqueue",
    )

    for table_name in ("workspace", "project", "task"):
        op_ext.add_column_if_not_exists(
            table_name,
            sa.Column("policy_id", sa.UUID(), nullable=True),
            schema="agenticqueue",
        )
        op.create_foreign_key(
            op.f(f"fk_{table_name}_policy_id_policy"),
            source_table=table_name,
            referent_table="policy",
            local_cols=["policy_id"],
            remote_cols=["id"],
            source_schema="agenticqueue",
            referent_schema="agenticqueue",
            ondelete="SET NULL",
        )
        op_ext.create_index_if_not_exists(
            op.f(f"ix_{table_name}_policy_id"),
            table_name,
            ["policy_id"],
            unique=False,
            schema="agenticqueue",
        )


def downgrade() -> None:
    for table_name in ("task", "project", "workspace"):
        op_ext.drop_index_if_exists(
            op.f(f"ix_{table_name}_policy_id"),
            table_name=table_name,
            schema="agenticqueue",
        )
        op_ext.drop_constraint_if_exists(
            op.f(f"fk_{table_name}_policy_id_policy"),
            table_name,
            schema="agenticqueue",
            type_="foreignkey",
        )
        op_ext.drop_column_if_exists(table_name, "policy_id", schema="agenticqueue")

    op_ext.drop_column_if_exists("policy", "capabilities", schema="agenticqueue")
