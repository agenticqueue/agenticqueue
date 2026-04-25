"""Add task retry tracking fields."""

from __future__ import annotations

import op_ext

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260421_20"
down_revision = "20260420_19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op_ext.add_column_if_not_exists(
        "task",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema="agenticqueue",
    )
    op_ext.add_column_if_not_exists(
        "task",
        sa.Column(
            "last_failure",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        schema="agenticqueue",
    )
    op.alter_column(
        "task",
        "attempt_count",
        schema="agenticqueue",
        server_default=None,
    )


def downgrade() -> None:
    op_ext.drop_column_if_exists("task", "last_failure", schema="agenticqueue")
    op_ext.drop_column_if_exists("task", "attempt_count", schema="agenticqueue")
