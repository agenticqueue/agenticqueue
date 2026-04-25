"""Replace local user usernames with email identifiers."""

from __future__ import annotations

import op_ext

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260424_29"
down_revision = "20260423_28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op_ext.add_column_if_not_exists(
        "users",
        sa.Column("email", postgresql.CITEXT(), nullable=True),
        schema="agenticqueue",
    )
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'agenticqueue'
                  AND table_name = 'users'
                  AND column_name = 'username'
            ) THEN
                UPDATE agenticqueue.users SET email = username WHERE email IS NULL;
            END IF;
        END $$;
    """)
    op.alter_column("users", "email", nullable=False, schema="agenticqueue")
    op_ext.drop_constraint_if_exists(
        op.f("uq_users_username"),
        "users",
        schema="agenticqueue",
        type_="unique",
    )
    op_ext.create_unique_constraint_if_not_exists(
        op.f("uq_users_email"),
        "users",
        ["email"],
        schema="agenticqueue",
    )
    op_ext.drop_column_if_exists("users", "username", schema="agenticqueue")


def downgrade() -> None:
    op_ext.add_column_if_not_exists(
        "users",
        sa.Column("username", sa.String(length=120), nullable=True),
        schema="agenticqueue",
    )
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'agenticqueue'
                  AND table_name = 'users'
                  AND column_name = 'email'
            ) THEN
                UPDATE agenticqueue.users SET username = email::text WHERE username IS NULL;
            END IF;
        END $$;
    """)
    op.alter_column("users", "username", nullable=False, schema="agenticqueue")
    op_ext.drop_constraint_if_exists(
        op.f("uq_users_email"),
        "users",
        schema="agenticqueue",
        type_="unique",
    )
    op_ext.create_unique_constraint_if_not_exists(
        op.f("uq_users_username"),
        "users",
        ["username"],
        schema="agenticqueue",
    )
    op_ext.drop_column_if_exists("users", "email", schema="agenticqueue")
