"""Harden audit_log into an append-only before/after ledger."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260420_04"
down_revision = "20260420_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_log",
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="agenticqueue",
    )
    op.add_column(
        "audit_log",
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="agenticqueue",
    )
    op.add_column(
        "audit_log",
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        schema="agenticqueue",
    )
    op.execute(
        'UPDATE agenticqueue.audit_log SET "after" = payload WHERE payload IS NOT NULL'
    )
    op.drop_column("audit_log", "payload", schema="agenticqueue")

    op.execute(
        """
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
             AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at THEN
            RETURN NEW;
          END IF;

          RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_append_only
        BEFORE UPDATE OR DELETE ON agenticqueue.audit_log
        FOR EACH ROW
        EXECUTE FUNCTION agenticqueue.prevent_audit_log_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS audit_log_append_only ON agenticqueue.audit_log"
    )
    op.execute("DROP FUNCTION IF EXISTS agenticqueue.prevent_audit_log_mutation()")

    op.add_column(
        "audit_log",
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="agenticqueue",
    )
    op.execute(
        """
        UPDATE agenticqueue.audit_log
        SET payload = COALESCE("after", "before", '{}'::jsonb)
        """
    )
    op.alter_column("audit_log", "payload", nullable=False, schema="agenticqueue")
    op.drop_column("audit_log", "trace_id", schema="agenticqueue")
    op.drop_column("audit_log", "after", schema="agenticqueue")
    op.drop_column("audit_log", "before", schema="agenticqueue")
