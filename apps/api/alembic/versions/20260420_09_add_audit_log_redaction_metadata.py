"""Add secret-redaction metadata to audit_log rows."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260420_09"
down_revision = "20260420_08"
branch_labels = None
depends_on = None


def _create_append_only_function(include_redaction: bool) -> None:
    redaction_guard = (
        'AND NEW.redaction IS NOT DISTINCT FROM OLD.redaction\n'
        if include_redaction
        else ""
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION agenticqueue.prevent_audit_log_mutation()
        RETURNS trigger
        AS $$
        BEGIN
          IF TG_OP = 'UPDATE'
             AND NEW.actor_id IS NULL
             AND OLD.actor_id IS NOT NULL
             AND NEW.id = OLD.id
             AND NEW.entity_type IS NOT DISTINCT FROM OLD.entity_type
             AND NEW.entity_id IS NOT DISTINCT FROM OLD.entity_id
             AND NEW.action IS NOT DISTINCT FROM OLD.action
             AND NEW."before" IS NOT DISTINCT FROM OLD."before"
             AND NEW."after" IS NOT DISTINCT FROM OLD."after"
             AND NEW.trace_id IS NOT DISTINCT FROM OLD.trace_id
             {redaction_guard}AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at THEN
            RETURN NEW;
          END IF;

          RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def upgrade() -> None:
    op.add_column(
        "audit_log",
        sa.Column("redaction", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="agenticqueue",
    )
    _create_append_only_function(include_redaction=True)


def downgrade() -> None:
    _create_append_only_function(include_redaction=False)
    op.drop_column("audit_log", "redaction", schema="agenticqueue")
