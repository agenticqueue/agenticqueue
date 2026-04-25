"""Add read-model indexes for analytics queries."""

from __future__ import annotations

import op_ext

from alembic import op

revision = "20260421_21"
down_revision = "20260421_20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op_ext.create_index_if_not_exists(
        "ix_task_state_updated_at",
        "task",
        ["state", "updated_at"],
        unique=False,
        schema="agenticqueue",
    )
    op_ext.create_index_if_not_exists(
        "ix_task_type_state_updated_at",
        "task",
        ["task_type", "state", "updated_at"],
        unique=False,
        schema="agenticqueue",
    )
    op_ext.create_index_if_not_exists(
        "ix_run_ended_at",
        "run",
        ["ended_at"],
        unique=False,
        schema="agenticqueue",
    )
    op_ext.create_index_if_not_exists(
        "ix_run_actor_ended_at",
        "run",
        ["actor_id", "ended_at"],
        unique=False,
        schema="agenticqueue",
    )
    op_ext.create_index_if_not_exists(
        "ix_packet_version_created_at",
        "packet_version",
        ["created_at"],
        unique=False,
        schema="agenticqueue",
    )


def downgrade() -> None:
    op_ext.drop_index_if_exists(
        "ix_packet_version_created_at",
        table_name="packet_version",
        schema="agenticqueue",
    )
    op_ext.drop_index_if_exists(
        "ix_run_actor_ended_at",
        table_name="run",
        schema="agenticqueue",
    )
    op_ext.drop_index_if_exists(
        "ix_run_ended_at",
        table_name="run",
        schema="agenticqueue",
    )
    op_ext.drop_index_if_exists(
        "ix_task_type_state_updated_at",
        table_name="task",
        schema="agenticqueue",
    )
    op_ext.drop_index_if_exists(
        "ix_task_state_updated_at",
        table_name="task",
        schema="agenticqueue",
    )
