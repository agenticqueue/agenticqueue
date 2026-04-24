"""Create cookie-backed human auth sessions."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260423_24"
down_revision = "20260423_23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("session_token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["agenticqueue.users.id"],
            name=op.f("fk_auth_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
        sa.UniqueConstraint(
            "session_token_hash",
            name=op.f("uq_auth_sessions_session_token_hash"),
        ),
        schema="agenticqueue",
    )
    op.create_index(
        op.f("ix_auth_sessions_expires_at"),
        "auth_sessions",
        ["expires_at"],
        unique=False,
        schema="agenticqueue",
    )
    op.create_index(
        op.f("ix_auth_sessions_user_id"),
        "auth_sessions",
        ["user_id"],
        unique=False,
        schema="agenticqueue",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_auth_sessions_user_id"),
        table_name="auth_sessions",
        schema="agenticqueue",
    )
    op.drop_index(
        op.f("ix_auth_sessions_expires_at"),
        table_name="auth_sessions",
        schema="agenticqueue",
    )
    op.drop_table("auth_sessions", schema="agenticqueue")
