"""Add idempotency cache rows for replay-safe mutations."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260420_08"
down_revision = "20260420_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "idempotency_key",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body_sha256", postgresql.BYTEA(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", sa.Text(), nullable=False),
        sa.Column(
            "replay_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["agenticqueue.actor.id"],
            ondelete="CASCADE",
            name=op.f("fk_idempotency_key_actor_id_actor"),
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_idempotency_key")),
        schema="agenticqueue",
    )
    op.create_index(
        op.f("ix_idempotency_key_actor_id"),
        "idempotency_key",
        ["actor_id"],
        unique=False,
        schema="agenticqueue",
    )
    op.create_index(
        op.f("ix_idempotency_key_expires_at"),
        "idempotency_key",
        ["expires_at"],
        unique=False,
        schema="agenticqueue",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_idempotency_key_expires_at"),
        table_name="idempotency_key",
        schema="agenticqueue",
    )
    op.drop_index(
        op.f("ix_idempotency_key_actor_id"),
        table_name="idempotency_key",
        schema="agenticqueue",
    )
    op.drop_table("idempotency_key", schema="agenticqueue")
