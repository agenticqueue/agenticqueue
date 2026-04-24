"""Add workspace ownership to actors."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260423_27"
down_revision = "20260423_26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "actor",
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        schema="agenticqueue",
    )
    op.create_foreign_key(
        op.f("fk_actor_workspace_id_workspace"),
        "actor",
        "workspace",
        ["workspace_id"],
        ["id"],
        source_schema="agenticqueue",
        referent_schema="agenticqueue",
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_actor_workspace_id"),
        "actor",
        ["workspace_id"],
        unique=False,
        schema="agenticqueue",
    )
    op.execute(
        """
        UPDATE agenticqueue.actor AS actor_row
        SET workspace_id = first_workspace.id
        FROM (
            SELECT id
            FROM agenticqueue.workspace
            ORDER BY created_at ASC, id ASC
            LIMIT 1
        ) AS first_workspace
        WHERE actor_row.workspace_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_actor_workspace_id"),
        table_name="actor",
        schema="agenticqueue",
    )
    op.drop_constraint(
        op.f("fk_actor_workspace_id_workspace"),
        "actor",
        schema="agenticqueue",
        type_="foreignkey",
    )
    op.drop_column("actor", "workspace_id", schema="agenticqueue")
