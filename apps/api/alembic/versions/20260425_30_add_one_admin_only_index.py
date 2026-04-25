"""Add DB-level single-admin enforcement."""

from __future__ import annotations

import op_ext

import sqlalchemy as sa

revision = "20260425_30"
down_revision = "20260424_29"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op_ext.create_index_if_not_exists(
        "one_admin_only",
        "users",
        ["is_admin"],
        unique=True,
        schema="agenticqueue",
        postgresql_where=sa.text("is_admin = true"),
    )


def downgrade() -> None:
    op_ext.drop_index_if_exists(
        "one_admin_only",
        table_name="users",
        schema="agenticqueue",
    )
