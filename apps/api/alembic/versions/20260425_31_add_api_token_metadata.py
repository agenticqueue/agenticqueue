"""Add API token names and last-used timestamps."""

from __future__ import annotations

import op_ext

from alembic import op
import sqlalchemy as sa

revision = "20260425_31"
down_revision = "20260425_30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op_ext.add_column_if_not_exists(
        "api_token",
        sa.Column("name", sa.String(length=120), nullable=True),
        schema="agenticqueue",
    )
    op_ext.add_column_if_not_exists(
        "api_token",
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        schema="agenticqueue",
    )
    op.execute("""
        UPDATE agenticqueue.api_token
        SET name = 'bootstrap'
        WHERE name IS NULL
    """)
    op.alter_column("api_token", "name", nullable=False, schema="agenticqueue")


def downgrade() -> None:
    op_ext.drop_column_if_exists("api_token", "last_used_at", schema="agenticqueue")
    op_ext.drop_column_if_exists("api_token", "name", schema="agenticqueue")
