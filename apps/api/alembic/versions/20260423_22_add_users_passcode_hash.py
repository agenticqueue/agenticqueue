"""Add local users with Argon2id passcode hashes."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260423_22"
down_revision = "20260421_21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("username", sa.String(length=120), nullable=False),
        sa.Column("passcode_hash", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column(
            "is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
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
            ["actor_id"],
            ["agenticqueue.actor.id"],
            name=op.f("fk_users_actor_id_actor"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
        schema="agenticqueue",
    )
    op.create_index(
        op.f("ix_users_actor_id"),
        "users",
        ["actor_id"],
        unique=False,
        schema="agenticqueue",
    )
    op.alter_column(
        "api_token",
        "token_hash",
        existing_type=sa.String(length=64),
        type_=sa.Text(),
        existing_nullable=False,
        schema="agenticqueue",
    )


def downgrade() -> None:
    op.alter_column(
        "api_token",
        "token_hash",
        existing_type=sa.Text(),
        type_=sa.String(length=64),
        existing_nullable=False,
        schema="agenticqueue",
        postgresql_using="left(token_hash, 64)",
    )
    op.drop_index(op.f("ix_users_actor_id"), table_name="users", schema="agenticqueue")
    op.drop_table("users", schema="agenticqueue")
