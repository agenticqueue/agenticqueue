"""Link runs back to the exact packet version they used."""

from __future__ import annotations

import op_ext

from alembic import op
import sqlalchemy as sa

revision = "20260420_15"
down_revision = "20260420_14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op_ext.add_column_if_not_exists(
        "run",
        sa.Column("packet_version_id", sa.UUID(), nullable=True),
        schema="agenticqueue",
    )
    op.create_foreign_key(
        op.f("fk_run_packet_version_id_packet_version"),
        "run",
        "packet_version",
        ["packet_version_id"],
        ["id"],
        source_schema="agenticqueue",
        referent_schema="agenticqueue",
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op_ext.drop_constraint_if_exists(
        op.f("fk_run_packet_version_id_packet_version"),
        "run",
        schema="agenticqueue",
        type_="foreignkey",
    )
    op_ext.drop_column_if_exists("run", "packet_version_id", schema="agenticqueue")
