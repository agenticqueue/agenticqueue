"""Reshape capability storage into catalog and grant tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260420_06"
down_revision = "20260420_05"
branch_labels = None
depends_on = None

STANDARD_CAPABILITIES = (
    ("read_repo", "Read repository contents."),
    ("write_branch", "Write code changes to the repository branch."),
    ("run_tests", "Run verification and test commands."),
    ("query_graph", "Query graph lineage and dependency data."),
    ("search_memory", "Search stored learnings and memory."),
    ("create_artifact", "Create artifacts linked to task runs."),
    ("update_task", "Update task state and metadata."),
    ("trigger_handoff", "Trigger downstream handoffs or dispatches."),
    ("read_learnings", "Read structured learnings."),
    ("write_learning", "Write new task or project learnings."),
    ("promote_learning", "Promote a learning to broader scope."),
    ("admin", "Perform privileged administrative actions."),
)


def upgrade() -> None:
    op.rename_table("capability", "capability_legacy", schema="agenticqueue")
    op.execute(
        "ALTER TABLE agenticqueue.capability_legacy "
        "RENAME CONSTRAINT pk_capability TO pk_capability_legacy"
    )
    op.execute(
        "ALTER TABLE agenticqueue.capability_legacy "
        "RENAME CONSTRAINT fk_capability_actor_id_actor "
        "TO fk_capability_legacy_actor_id_actor"
    )
    op.execute(
        "ALTER TABLE agenticqueue.capability_legacy "
        "RENAME CONSTRAINT fk_capability_granted_by_actor_id_actor "
        "TO fk_capability_legacy_granted_by_actor_id_actor"
    )

    op.create_table(
        "capability",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capability")),
        sa.UniqueConstraint("key", name="uq_capability_key"),
        schema="agenticqueue",
    )
    op.create_table(
        "capability_grant",
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("capability_id", sa.UUID(), nullable=False),
        sa.Column(
            "scope",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("granted_by_actor_id", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_capability_grant_actor_id_actor"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["capability_id"],
            ["agenticqueue.capability.id"],
            name=op.f("fk_capability_grant_capability_id_capability"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_actor_id"],
            ["agenticqueue.actor.id"],
            name=op.f("fk_capability_grant_granted_by_actor_id_actor"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capability_grant")),
        schema="agenticqueue",
    )
    op.create_index(
        "ix_capability_grant_actor_id",
        "capability_grant",
        ["actor_id"],
        unique=False,
        schema="agenticqueue",
    )
    op.create_index(
        "ix_capability_grant_capability_id",
        "capability_grant",
        ["capability_id"],
        unique=False,
        schema="agenticqueue",
    )

    capability_table = sa.table(
        "capability",
        sa.column("key", sa.String()),
        sa.column("description", sa.Text()),
    )
    op.bulk_insert(
        capability_table,
        [{"key": key, "description": description} for key, description in STANDARD_CAPABILITIES],
    )
    op.execute("""
        INSERT INTO agenticqueue.capability (key, description)
        SELECT DISTINCT legacy.capability_key, 'Imported legacy capability'
        FROM agenticqueue.capability_legacy AS legacy
        WHERE NOT EXISTS (
            SELECT 1
            FROM agenticqueue.capability AS catalog
            WHERE catalog.key = legacy.capability_key
        )
        """)
    op.execute("""
        INSERT INTO agenticqueue.capability_grant (
            id,
            actor_id,
            capability_id,
            scope,
            granted_by_actor_id,
            expires_at,
            revoked_at,
            created_at,
            updated_at
        )
        SELECT
            legacy.id,
            legacy.actor_id,
            catalog.id,
            jsonb_build_object('legacy_scope', legacy.scope),
            legacy.granted_by_actor_id,
            NULL,
            CASE WHEN legacy.is_active THEN NULL ELSE legacy.updated_at END,
            legacy.created_at,
            legacy.updated_at
        FROM agenticqueue.capability_legacy AS legacy
        JOIN agenticqueue.capability AS catalog
          ON catalog.key = legacy.capability_key
        """)
    op.drop_table("capability_legacy", schema="agenticqueue")


def downgrade() -> None:
    op.create_table(
        "capability_legacy",
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("capability_key", sa.String(length=120), nullable=False),
        sa.Column("scope", sa.String(length=255), nullable=False),
        sa.Column("granted_by_actor_id", sa.UUID(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["agenticqueue.actor.id"],
            name="fk_capability_legacy_actor_id_actor",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_actor_id"],
            ["agenticqueue.actor.id"],
            name="fk_capability_legacy_granted_by_actor_id_actor",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_capability_legacy"),
        schema="agenticqueue",
    )
    op.execute("""
        INSERT INTO agenticqueue.capability_legacy (
            id,
            actor_id,
            capability_key,
            scope,
            granted_by_actor_id,
            is_active,
            created_at,
            updated_at
        )
        SELECT
            grant_row.id,
            grant_row.actor_id,
            catalog.key,
            COALESCE(grant_row.scope->>'legacy_scope', grant_row.scope::text),
            grant_row.granted_by_actor_id,
            CASE WHEN grant_row.revoked_at IS NULL THEN true ELSE false END,
            grant_row.created_at,
            COALESCE(grant_row.revoked_at, grant_row.updated_at)
        FROM agenticqueue.capability_grant AS grant_row
        JOIN agenticqueue.capability AS catalog
          ON catalog.id = grant_row.capability_id
        """)

    op.drop_index(
        "ix_capability_grant_capability_id",
        table_name="capability_grant",
        schema="agenticqueue",
    )
    op.drop_index(
        "ix_capability_grant_actor_id",
        table_name="capability_grant",
        schema="agenticqueue",
    )
    op.drop_table("capability_grant", schema="agenticqueue")
    op.drop_table("capability", schema="agenticqueue")
    op.rename_table("capability_legacy", "capability", schema="agenticqueue")
    op.execute(
        "ALTER TABLE agenticqueue.capability "
        "RENAME CONSTRAINT pk_capability_legacy TO pk_capability"
    )
    op.execute(
        "ALTER TABLE agenticqueue.capability "
        "RENAME CONSTRAINT fk_capability_legacy_actor_id_actor "
        "TO fk_capability_actor_id_actor"
    )
    op.execute(
        "ALTER TABLE agenticqueue.capability "
        "RENAME CONSTRAINT fk_capability_legacy_granted_by_actor_id_actor "
        "TO fk_capability_granted_by_actor_id_actor"
    )
