"""Backfill the standard capability catalog rows at head."""

from __future__ import annotations

from alembic import op

revision = "20260420_16"
down_revision = "20260420_15"
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
    values = ",\n        ".join(
        f"('{key}', '{description}')" for key, description in STANDARD_CAPABILITIES
    )
    op.execute(f"""
        INSERT INTO agenticqueue.capability (key, description)
        VALUES
        {values}
        ON CONFLICT (key) DO NOTHING
        """)


def downgrade() -> None:
    keys = ", ".join(f"'{key}'" for key, _ in STANDARD_CAPABILITIES)
    op.execute(f"""
        DELETE FROM agenticqueue.capability AS capability
        WHERE capability.key IN ({keys})
          AND NOT EXISTS (
              SELECT 1
              FROM agenticqueue.capability_grant AS grant_row
              WHERE grant_row.capability_id = capability.id
          )
        """)
