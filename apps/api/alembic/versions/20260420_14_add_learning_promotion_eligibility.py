"""Add persisted learning promotion eligibility flag."""

from __future__ import annotations

import op_ext

import sqlalchemy as sa

revision = "20260420_14"
down_revision = "20260420_13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op_ext.add_column_if_not_exists(
        "learning",
        sa.Column(
            "promotion_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="agenticqueue",
    )


def downgrade() -> None:
    op_ext.drop_column_if_exists(
        "learning", "promotion_eligible", schema="agenticqueue"
    )
