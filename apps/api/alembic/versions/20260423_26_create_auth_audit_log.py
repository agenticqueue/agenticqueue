"""Create auth audit log with user-or-actor attribution."""

from __future__ import annotations

import op_ext

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260423_26"
down_revision = "20260423_25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op_ext.create_table_if_not_exists(
        "auth_audit_log",
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "user_id IS NOT NULL OR actor_id IS NOT NULL",
            name="ck_auth_audit_log_has_subject",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["agenticqueue.actor.id"],
            name=op.f("fk_auth_audit_log_actor_id_actor"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["agenticqueue.users.id"],
            name=op.f("fk_auth_audit_log_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_audit_log")),
        schema="agenticqueue",
    )
    op_ext.create_index_if_not_exists(
        op.f("ix_auth_audit_log_actor_id"),
        "auth_audit_log",
        ["actor_id"],
        unique=False,
        schema="agenticqueue",
    )
    op_ext.create_index_if_not_exists(
        op.f("ix_auth_audit_log_user_id"),
        "auth_audit_log",
        ["user_id"],
        unique=False,
        schema="agenticqueue",
    )


def downgrade() -> None:
    op_ext.drop_index_if_exists(
        op.f("ix_auth_audit_log_user_id"),
        table_name="auth_audit_log",
        schema="agenticqueue",
    )
    op_ext.drop_index_if_exists(
        op.f("ix_auth_audit_log_actor_id"),
        table_name="auth_audit_log",
        schema="agenticqueue",
    )
    op_ext.drop_table_if_exists("auth_audit_log", schema="agenticqueue")
