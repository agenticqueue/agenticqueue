"""Add persisted learning promotion eligibility flag."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260420_14"
down_revision = "20260420_13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
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
    op.drop_column("learning", "promotion_eligible", schema="agenticqueue")
