"""Add seeded RBAC roles and actor-role assignments."""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260420_19"
down_revision = "20260420_18"
branch_labels = None
depends_on = None

STANDARD_ROLES = (
    {
        "name": "admin",
        "description": "Full administrative access across all AgenticQueue surfaces.",
        "capabilities": [
            "read_repo",
            "write_branch",
            "run_tests",
            "query_graph",
            "search_memory",
            "create_artifact",
            "update_task",
            "trigger_handoff",
            "read_learnings",
            "write_learning",
            "promote_learning",
            "admin",
        ],
        "scope": {},
    },
    {
        "name": "maintainer",
        "description": "Ship work, manage learnings, and drive handoffs without admin-only powers.",
        "capabilities": [
            "read_repo",
            "write_branch",
            "run_tests",
            "query_graph",
            "search_memory",
            "create_artifact",
            "update_task",
            "trigger_handoff",
            "read_learnings",
            "write_learning",
            "promote_learning",
        ],
        "scope": {},
    },
    {
        "name": "contributor",
        "description": "Implement scoped coding work and write task-scoped learnings.",
        "capabilities": [
            "read_repo",
            "write_branch",
            "run_tests",
            "create_artifact",
            "update_task",
            "read_learnings",
            "write_learning",
        ],
        "scope": {},
    },
    {
        "name": "reviewer",
        "description": "Inspect runs, validate changes, and promote reusable learnings.",
        "capabilities": [
            "read_repo",
            "run_tests",
            "query_graph",
            "search_memory",
            "read_learnings",
            "promote_learning",
        ],
        "scope": {},
    },
    {
        "name": "read-only",
        "description": "Inspect repository, graph, and learnings state without write access.",
        "capabilities": [
            "read_repo",
            "query_graph",
            "search_memory",
            "read_learnings",
        ],
        "scope": {},
    },
    {
        "name": "bot",
        "description": "Automation-friendly bundle for repo, artifact, and task mutation work.",
        "capabilities": [
            "read_repo",
            "write_branch",
            "run_tests",
            "query_graph",
            "search_memory",
            "create_artifact",
            "update_task",
            "trigger_handoff",
            "read_learnings",
            "write_learning",
        ],
        "scope": {},
    },
)


def upgrade() -> None:
    op.create_table(
        "role",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "scope",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role")),
        sa.UniqueConstraint("name", name="uq_role_name"),
        schema="agenticqueue",
    )
    op.create_table(
        "actor_role_assignment",
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
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
            name=op.f("fk_actor_role_assignment_actor_id_actor"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["agenticqueue.role.id"],
            name=op.f("fk_actor_role_assignment_role_id_role"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_actor_id"],
            ["agenticqueue.actor.id"],
            name=op.f("fk_actor_role_assignment_granted_by_actor_id_actor"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_actor_role_assignment")),
        schema="agenticqueue",
    )
    op.create_index(
        "ix_actor_role_assignment_actor_id",
        "actor_role_assignment",
        ["actor_id"],
        unique=False,
        schema="agenticqueue",
    )
    op.create_index(
        "ix_actor_role_assignment_role_id",
        "actor_role_assignment",
        ["role_id"],
        unique=False,
        schema="agenticqueue",
    )
    op.add_column(
        "capability_grant",
        sa.Column("role_assignment_id", sa.UUID(), nullable=True),
        schema="agenticqueue",
    )
    op.create_foreign_key(
        op.f("fk_capability_grant_role_assignment_id_actor_role_assignment"),
        "capability_grant",
        "actor_role_assignment",
        ["role_assignment_id"],
        ["id"],
        source_schema="agenticqueue",
        referent_schema="agenticqueue",
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_capability_grant_role_assignment_id",
        "capability_grant",
        ["role_assignment_id"],
        unique=False,
        schema="agenticqueue",
    )

    insert_role = sa.text(
        """
        INSERT INTO agenticqueue.role (name, description, capabilities, scope)
        VALUES (:name, :description, CAST(:capabilities AS jsonb), CAST(:scope AS jsonb))
        """
    )
    for role in STANDARD_ROLES:
        op.execute(
            insert_role.bindparams(
                name=role["name"],
                description=role["description"],
                capabilities=json.dumps(role["capabilities"]),
                scope=json.dumps(role["scope"]),
            )
        )


def downgrade() -> None:
    op.drop_index(
        "ix_capability_grant_role_assignment_id",
        table_name="capability_grant",
        schema="agenticqueue",
    )
    op.drop_constraint(
        op.f("fk_capability_grant_role_assignment_id_actor_role_assignment"),
        "capability_grant",
        schema="agenticqueue",
        type_="foreignkey",
    )
    op.drop_column("capability_grant", "role_assignment_id", schema="agenticqueue")

    op.drop_index(
        "ix_actor_role_assignment_role_id",
        table_name="actor_role_assignment",
        schema="agenticqueue",
    )
    op.drop_index(
        "ix_actor_role_assignment_actor_id",
        table_name="actor_role_assignment",
        schema="agenticqueue",
    )
    op.drop_table("actor_role_assignment", schema="agenticqueue")
    op.drop_table("role", schema="agenticqueue")
