"""Add WORM hash-chain guarantees to audit_log."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260420_11"
down_revision = "20260420_10"
branch_labels = None
depends_on = None

ZERO_HASH_SQL = "decode(repeat('00', 32), 'hex')"


def upgrade() -> None:
    op.add_column(
        "audit_log",
        sa.Column("chain_position", sa.BigInteger(), nullable=True),
        schema="agenticqueue",
    )
    op.add_column(
        "audit_log",
        sa.Column("prev_hash", postgresql.BYTEA(), nullable=True),
        schema="agenticqueue",
    )
    op.add_column(
        "audit_log",
        sa.Column("row_hash", postgresql.BYTEA(), nullable=True),
        schema="agenticqueue",
    )
    op.execute("DROP TRIGGER IF EXISTS audit_log_append_only ON agenticqueue.audit_log")
    op.execute(
        """
        CREATE SEQUENCE IF NOT EXISTS agenticqueue.audit_log_chain_position_seq
        AS bigint
        START WITH 1
        INCREMENT BY 1
        NO MINVALUE
        NO MAXVALUE
        CACHE 1
        """
    )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION agenticqueue.audit_log_chain_digest(
          previous_hash bytea,
          audit_id uuid,
          audit_created_at timestamptz,
          audit_actor_id uuid,
          audit_entity_type text,
          audit_entity_id uuid,
          audit_action text,
          audit_before jsonb,
          audit_after jsonb,
          audit_trace_id text,
          audit_redaction jsonb
        )
        RETURNS bytea
        LANGUAGE SQL
        IMMUTABLE
        AS $$
          SELECT digest(
            COALESCE(previous_hash, {ZERO_HASH_SQL}) ||
            convert_to(
              jsonb_build_object(
                'id', audit_id,
                'created_at', audit_created_at,
                'actor_id', audit_actor_id,
                'entity_type', audit_entity_type,
                'entity_id', audit_entity_id,
                'action', audit_action,
                'before', audit_before,
                'after', audit_after,
                'trace_id', audit_trace_id,
                'redaction', audit_redaction
              )::text,
              'UTF8'
            ),
            'sha256'
          )
        $$;
        """
    )

    op.execute(
        f"""
        WITH RECURSIVE ordered AS (
          SELECT
            audit_log.id,
            audit_log.created_at,
            audit_log.actor_id,
            audit_log.entity_type,
            audit_log.entity_id,
            audit_log.action,
            audit_log."before",
            audit_log."after",
            audit_log.trace_id,
            audit_log.redaction,
            row_number() OVER (ORDER BY audit_log.created_at, audit_log.id) AS position
          FROM agenticqueue.audit_log AS audit_log
        ),
        chain AS (
          SELECT
            ordered.id,
            ordered.position,
            {ZERO_HASH_SQL} AS prev_hash,
            agenticqueue.audit_log_chain_digest(
              {ZERO_HASH_SQL},
              ordered.id,
              ordered.created_at,
              ordered.actor_id,
              ordered.entity_type,
              ordered.entity_id,
              ordered.action,
              ordered."before",
              ordered."after",
              ordered.trace_id,
              ordered.redaction
            ) AS row_hash
          FROM ordered
          WHERE ordered.position = 1

          UNION ALL

          SELECT
            ordered.id,
            ordered.position,
            chain.row_hash AS prev_hash,
            agenticqueue.audit_log_chain_digest(
              chain.row_hash,
              ordered.id,
              ordered.created_at,
              ordered.actor_id,
              ordered.entity_type,
              ordered.entity_id,
              ordered.action,
              ordered."before",
              ordered."after",
              ordered.trace_id,
              ordered.redaction
            ) AS row_hash
          FROM ordered
          JOIN chain ON ordered.position = chain.position + 1
        )
        UPDATE agenticqueue.audit_log AS audit_log
        SET
          chain_position = chain.position,
          prev_hash = chain.prev_hash,
          row_hash = chain.row_hash
        FROM chain
        WHERE audit_log.id = chain.id;
        """
    )

    op.alter_column("audit_log", "chain_position", nullable=False, schema="agenticqueue")
    op.alter_column("audit_log", "prev_hash", nullable=False, schema="agenticqueue")
    op.alter_column("audit_log", "row_hash", nullable=False, schema="agenticqueue")
    op.create_index(
        "uq_audit_log_chain_position",
        "audit_log",
        ["chain_position"],
        unique=True,
        schema="agenticqueue",
    )
    op.execute(
        """
        SELECT setval(
          'agenticqueue.audit_log_chain_position_seq',
          COALESCE((SELECT MAX(chain_position) FROM agenticqueue.audit_log), 1),
          COALESCE((SELECT COUNT(*) > 0 FROM agenticqueue.audit_log), FALSE)
        )
        """
    )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION agenticqueue.audit_log_set_chain_hashes()
        RETURNS trigger
        AS $$
        DECLARE
          previous_row_hash bytea;
        BEGIN
          PERFORM pg_advisory_xact_lock(90127, 174);
          NEW.chain_position := nextval('agenticqueue.audit_log_chain_position_seq');
          NEW.created_at := clock_timestamp();

          SELECT audit_log.row_hash
          INTO previous_row_hash
          FROM agenticqueue.audit_log AS audit_log
          ORDER BY audit_log.chain_position DESC
          LIMIT 1;

          NEW.prev_hash := COALESCE(previous_row_hash, {ZERO_HASH_SQL});
          NEW.row_hash := agenticqueue.audit_log_chain_digest(
            NEW.prev_hash,
            NEW.id,
            NEW.created_at,
            NEW.actor_id,
            NEW.entity_type,
            NEW.entity_id,
            NEW.action,
            NEW."before",
            NEW."after",
            NEW.trace_id,
            NEW.redaction
          );
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS audit_log_set_chain_hashes ON agenticqueue.audit_log")
    op.execute(
        """
        CREATE TRIGGER audit_log_set_chain_hashes
        BEFORE INSERT ON agenticqueue.audit_log
        FOR EACH ROW
        EXECUTE FUNCTION agenticqueue.audit_log_set_chain_hashes();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION agenticqueue.prevent_audit_log_mutation()
        RETURNS trigger
        AS $$
        BEGIN
          RAISE EXCEPTION
            USING MESSAGE = 'audit_log is append-only',
                  ERRCODE = '55000';
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

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION agenticqueue.verify_audit_log_chain()
        RETURNS TABLE (
          chain_length bigint,
          verified_count bigint,
          first_break_id_or_null uuid
        )
        LANGUAGE SQL
        STABLE
        AS $$
          WITH ordered AS (
            SELECT
              audit_log.id,
              audit_log.created_at,
              audit_log.actor_id,
              audit_log.entity_type,
              audit_log.entity_id,
              audit_log.action,
              audit_log."before",
              audit_log."after",
              audit_log.trace_id,
              audit_log.redaction,
              audit_log.chain_position,
              audit_log.prev_hash,
              audit_log.row_hash,
              row_number() OVER (ORDER BY audit_log.chain_position) AS position,
              COALESCE(
                lag(audit_log.row_hash) OVER (ORDER BY audit_log.chain_position),
                {ZERO_HASH_SQL}
              ) AS expected_prev_hash
            FROM agenticqueue.audit_log AS audit_log
          ),
          checks AS (
            SELECT
              ordered.id,
              ordered.position,
              ordered.prev_hash = ordered.expected_prev_hash
              AND ordered.row_hash = agenticqueue.audit_log_chain_digest(
                ordered.expected_prev_hash,
                ordered.id,
                ordered.created_at,
                ordered.actor_id,
                ordered.entity_type,
                ordered.entity_id,
                ordered.action,
                ordered."before",
                ordered."after",
                ordered.trace_id,
                ordered.redaction
              ) AS is_valid
            FROM ordered
          ),
          first_break AS (
            SELECT checks.id, checks.position
            FROM checks
            WHERE NOT checks.is_valid
            ORDER BY checks.position
            LIMIT 1
          )
          SELECT
            (SELECT COUNT(*) FROM ordered) AS chain_length,
            COALESCE(
              (SELECT first_break.position - 1 FROM first_break),
              (SELECT COUNT(*) FROM ordered)
            ) AS verified_count,
            (SELECT first_break.id FROM first_break) AS first_break_id_or_null
        $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agenticqueue_app') THEN
            CREATE ROLE agenticqueue_app NOLOGIN;
          END IF;
        END
        $$;
        """
    )
    op.execute("GRANT agenticqueue_app TO CURRENT_USER")
    op.execute("GRANT USAGE ON SCHEMA agenticqueue TO agenticqueue_app")
    op.execute("GRANT SELECT, INSERT ON agenticqueue.audit_log TO agenticqueue_app")
    op.execute(
        "GRANT EXECUTE ON FUNCTION agenticqueue.verify_audit_log_chain() "
        "TO agenticqueue_app"
    )
    op.execute("REVOKE UPDATE, DELETE ON agenticqueue.audit_log FROM agenticqueue_app")


def downgrade() -> None:
    op.execute(
        "REVOKE EXECUTE ON FUNCTION agenticqueue.verify_audit_log_chain() "
        "FROM agenticqueue_app"
    )
    op.execute("REVOKE SELECT, INSERT ON agenticqueue.audit_log FROM agenticqueue_app")
    op.execute("REVOKE USAGE ON SCHEMA agenticqueue FROM agenticqueue_app")

    op.execute("DROP FUNCTION IF EXISTS agenticqueue.verify_audit_log_chain()")
    op.execute("DROP TRIGGER IF EXISTS audit_log_set_chain_hashes ON agenticqueue.audit_log")
    op.execute("DROP FUNCTION IF EXISTS agenticqueue.audit_log_set_chain_hashes()")
    op.execute("DROP INDEX IF EXISTS agenticqueue.uq_audit_log_chain_position")
    op.execute("DROP SEQUENCE IF EXISTS agenticqueue.audit_log_chain_position_seq")
    op.execute(
        """
        DROP FUNCTION IF EXISTS agenticqueue.audit_log_chain_digest(
          bytea,
          uuid,
          timestamptz,
          uuid,
          text,
          uuid,
          text,
          jsonb,
          jsonb,
          text,
          jsonb
        )
        """
    )

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
             AND NEW.redaction IS NOT DISTINCT FROM OLD.redaction
             AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at THEN
            RETURN NEW;
          END IF;

          RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute("ALTER TABLE agenticqueue.audit_log DROP COLUMN IF EXISTS chain_position")
    op.drop_column("audit_log", "row_hash", schema="agenticqueue")
    op.drop_column("audit_log", "prev_hash", schema="agenticqueue")
