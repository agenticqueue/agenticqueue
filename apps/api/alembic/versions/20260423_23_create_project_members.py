"""Create many-to-many project memberships for local users."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260423_23"
down_revision = "20260423_22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_members",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
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
            ["project_id"],
            ["agenticqueue.project.id"],
            name=op.f("fk_project_members_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["agenticqueue.users.id"],
            name=op.f("fk_project_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_members")),
        sa.UniqueConstraint(
            "user_id",
            "project_id",
            name="uq_project_members_user_id_project_id",
        ),
        schema="agenticqueue",
    )
    op.create_index(
        op.f("ix_project_members_project_id"),
        "project_members",
        ["project_id"],
        unique=False,
        schema="agenticqueue",
    )
    op.create_index(
        op.f("ix_project_members_user_id"),
        "project_members",
        ["user_id"],
        unique=False,
        schema="agenticqueue",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_project_members_user_id"),
        table_name="project_members",
        schema="agenticqueue",
    )
    op.drop_index(
        op.f("ix_project_members_project_id"),
        table_name="project_members",
        schema="agenticqueue",
    )
    op.drop_table("project_members", schema="agenticqueue")
