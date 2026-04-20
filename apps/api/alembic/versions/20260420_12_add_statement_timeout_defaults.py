"""Persist the default statement timeout on the application role."""

from __future__ import annotations

from alembic import op

from agenticqueue_api.db import role_statement_timeout_ms

revision = "20260420_12"
down_revision = "20260420_11"
branch_labels = None
depends_on = None

ROLE_NAME = "agenticqueue"


def _alter_role_statement_timeout(*, reset: bool) -> None:
    timeout_value = f"{role_statement_timeout_ms()}ms"
    statement = (
        f"ALTER ROLE {ROLE_NAME} RESET statement_timeout"
        if reset
        else f"ALTER ROLE {ROLE_NAME} SET statement_timeout = ''{timeout_value}''"
    )
    op.execute(f"""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE_NAME}') THEN
        EXECUTE '{statement}';
      END IF;
    END
    $$;
    """)


def upgrade() -> None:
    _alter_role_statement_timeout(reset=False)


def downgrade() -> None:
    _alter_role_statement_timeout(reset=True)
