"""Pure capability-key definitions shared outside the ORM layer."""

from __future__ import annotations

from enum import StrEnum


class CapabilityKey(StrEnum):
    """Standard capability keys for AgenticQueue policies and grants."""

    READ_REPO = "read_repo"
    WRITE_BRANCH = "write_branch"
    RUN_TESTS = "run_tests"
    QUERY_GRAPH = "query_graph"
    SEARCH_MEMORY = "search_memory"
    CREATE_ARTIFACT = "create_artifact"
    UPDATE_TASK = "update_task"
    TRIGGER_HANDOFF = "trigger_handoff"
    READ_LEARNINGS = "read_learnings"
    WRITE_LEARNING = "write_learning"
    PROMOTE_LEARNING = "promote_learning"
    ADMIN = "admin"


STANDARD_CAPABILITY_DESCRIPTIONS: dict[CapabilityKey, str] = {
    CapabilityKey.READ_REPO: "Read repository contents.",
    CapabilityKey.WRITE_BRANCH: "Write code changes to the repository branch.",
    CapabilityKey.RUN_TESTS: "Run verification and test commands.",
    CapabilityKey.QUERY_GRAPH: "Query graph lineage and dependency data.",
    CapabilityKey.SEARCH_MEMORY: "Search stored learnings and memory.",
    CapabilityKey.CREATE_ARTIFACT: "Create artifacts linked to task runs.",
    CapabilityKey.UPDATE_TASK: "Update task state and metadata.",
    CapabilityKey.TRIGGER_HANDOFF: "Trigger downstream handoffs or dispatches.",
    CapabilityKey.READ_LEARNINGS: "Read structured learnings.",
    CapabilityKey.WRITE_LEARNING: "Write new task or project learnings.",
    CapabilityKey.PROMOTE_LEARNING: "Promote a learning to broader scope.",
    CapabilityKey.ADMIN: "Perform privileged administrative actions.",
}
