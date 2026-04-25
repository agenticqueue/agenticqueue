"""Create Postgres-backed login rate-limit table."""

from __future__ import annotations

import op_ext

from alembic import op
import sqlalchemy as sa

revision = "20260423_25"
down_revision = "20260423_24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op_ext.create_table_if_not_exists(
        "auth_rate_limit",
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("window_start_minute", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_rate_limit")),
        sa.UniqueConstraint(
            "ip",
            "window_start_minute",
            name="uq_auth_rate_limit_ip_window_start_minute",
        ),
        schema="agenticqueue",
    )
    op_ext.create_index_if_not_exists(
        op.f("ix_auth_rate_limit_ip"),
        "auth_rate_limit",
        ["ip"],
        unique=False,
        schema="agenticqueue",
    )
    op_ext.create_index_if_not_exists(
        op.f("ix_auth_rate_limit_window_start_minute"),
        "auth_rate_limit",
        ["window_start_minute"],
        unique=False,
        schema="agenticqueue",
    )


def downgrade() -> None:
    op_ext.drop_index_if_exists(
        op.f("ix_auth_rate_limit_window_start_minute"),
        table_name="auth_rate_limit",
        schema="agenticqueue",
    )
    op_ext.drop_index_if_exists(
        op.f("ix_auth_rate_limit_ip"),
        table_name="auth_rate_limit",
        schema="agenticqueue",
    )
    op_ext.drop_table_if_exists("auth_rate_limit", schema="agenticqueue")
