"""Replace local user usernames with email identifiers."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260424_29"
down_revision = "20260423_28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.add_column(
        "users",
        sa.Column("email", postgresql.CITEXT(), nullable=True),
        schema="agenticqueue",
    )
    op.execute("UPDATE agenticqueue.users SET email = username")
    op.alter_column("users", "email", nullable=False, schema="agenticqueue")
    op.drop_constraint(
        op.f("uq_users_username"),
        "users",
        schema="agenticqueue",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq_users_email"),
        "users",
        ["email"],
        schema="agenticqueue",
    )
    op.drop_column("users", "username", schema="agenticqueue")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("username", sa.String(length=120), nullable=True),
        schema="agenticqueue",
    )
    op.execute("UPDATE agenticqueue.users SET username = email::text")
    op.alter_column("users", "username", nullable=False, schema="agenticqueue")
    op.drop_constraint(
        op.f("uq_users_email"),
        "users",
        schema="agenticqueue",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq_users_username"),
        "users",
        ["username"],
        schema="agenticqueue",
    )
    op.drop_column("users", "email", schema="agenticqueue")
