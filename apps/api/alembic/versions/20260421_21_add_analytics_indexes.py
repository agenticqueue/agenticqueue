"""Add read-model indexes for analytics queries."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260421_21"
down_revision = "20260421_20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_task_state_updated_at",
        "task",
        ["state", "updated_at"],
        unique=False,
        schema="agenticqueue",
    )
    op.create_index(
        "ix_task_type_state_updated_at",
        "task",
        ["task_type", "state", "updated_at"],
        unique=False,
        schema="agenticqueue",
    )
    op.create_index(
        "ix_task_labels_gin",
        "task",
        ["labels"],
        unique=False,
        schema="agenticqueue",
        postgresql_using="gin",
    )
    op.create_index(
        "ix_run_ended_at",
        "run",
        ["ended_at"],
        unique=False,
        schema="agenticqueue",
    )
    op.create_index(
        "ix_run_actor_ended_at",
        "run",
        ["actor_id", "ended_at"],
        unique=False,
        schema="agenticqueue",
    )
    op.create_index(
        "ix_packet_version_created_at",
        "packet_version",
        ["created_at"],
        unique=False,
        schema="agenticqueue",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_packet_version_created_at",
        table_name="packet_version",
        schema="agenticqueue",
    )
    op.drop_index(
        "ix_run_actor_ended_at",
        table_name="run",
        schema="agenticqueue",
    )
    op.drop_index(
        "ix_run_ended_at",
        table_name="run",
        schema="agenticqueue",
    )
    op.drop_index(
        "ix_task_labels_gin",
        table_name="task",
        schema="agenticqueue",
    )
    op.drop_index(
        "ix_task_type_state_updated_at",
        table_name="task",
        schema="agenticqueue",
    )
    op.drop_index(
        "ix_task_state_updated_at",
        table_name="task",
        schema="agenticqueue",
    )
