"""Add task queue ordering and claim metadata."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260420_10"
down_revision = "20260420_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS agenticqueue.task_sequence_seq AS BIGINT")

    op.add_column(
        "task",
        sa.Column(
            "priority",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema="agenticqueue",
    )
    op.add_column(
        "task",
        sa.Column(
            "labels",
            postgresql.ARRAY(sa.String(length=120)),
            nullable=False,
            server_default=sa.text("ARRAY[]::varchar[]"),
        ),
        schema="agenticqueue",
    )
    op.add_column(
        "task",
        sa.Column(
            "sequence",
            sa.BigInteger(),
            nullable=True,
            server_default=sa.text("nextval('agenticqueue.task_sequence_seq')"),
        ),
        schema="agenticqueue",
    )
    op.add_column(
        "task",
        sa.Column("claimed_by_actor_id", sa.UUID(), nullable=True),
        schema="agenticqueue",
    )
    op.add_column(
        "task",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        schema="agenticqueue",
    )

    op.execute(
        "ALTER SEQUENCE agenticqueue.task_sequence_seq "
        "OWNED BY agenticqueue.task.sequence"
    )
    op.execute(
        "UPDATE agenticqueue.task "
        "SET sequence = nextval('agenticqueue.task_sequence_seq') "
        "WHERE sequence IS NULL"
    )
    op.alter_column("task", "sequence", nullable=False, schema="agenticqueue")

    op.create_foreign_key(
        op.f("fk_task_claimed_by_actor_id_actor"),
        "task",
        "actor",
        ["claimed_by_actor_id"],
        ["id"],
        source_schema="agenticqueue",
        referent_schema="agenticqueue",
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        op.f("uq_task_sequence"),
        "task",
        ["sequence"],
        schema="agenticqueue",
    )
    op.execute(
        "CREATE INDEX ix_task_queue_lookup "
        "ON agenticqueue.task (state, priority DESC, sequence ASC)"
    )
    op.execute(
        "CREATE INDEX ix_task_labels_gin ON agenticqueue.task USING gin (labels)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS agenticqueue.ix_task_labels_gin")
    op.execute("DROP INDEX IF EXISTS agenticqueue.ix_task_queue_lookup")
    op.drop_constraint(
        op.f("uq_task_sequence"),
        "task",
        schema="agenticqueue",
        type_="unique",
    )
    op.drop_constraint(
        op.f("fk_task_claimed_by_actor_id_actor"),
        "task",
        schema="agenticqueue",
        type_="foreignkey",
    )
    op.drop_column("task", "claimed_at", schema="agenticqueue")
    op.drop_column("task", "claimed_by_actor_id", schema="agenticqueue")
    op.drop_column("task", "sequence", schema="agenticqueue")
    op.drop_column("task", "labels", schema="agenticqueue")
    op.drop_column("task", "priority", schema="agenticqueue")
    op.execute("DROP SEQUENCE IF EXISTS agenticqueue.task_sequence_seq")
