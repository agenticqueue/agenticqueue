"""Merge actor workspace and local auth migration branches."""

from __future__ import annotations

revision = "20260423_28"
down_revision = ("20260423_27", "20260423_26")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op merge revision."""


def downgrade() -> None:
    """No-op merge revision."""
