"""Job commands backed by the task REST surface."""

from __future__ import annotations

from agenticqueue_cli.commands.factory import CommandSpec, build_group

SPECS = (
    CommandSpec(
        name="create",
        method="POST",
        path="/v1/tasks",
        help="Create one job/task from a JSON payload.",
        ok_statuses=(201,),
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="list",
        method="GET",
        path="/v1/tasks",
        help="List jobs/tasks.",
        accepts_filters=True,
        supports_pagination=True,
    ),
    CommandSpec(
        name="get",
        method="GET",
        path="/v1/tasks/{entity_id}",
        help="Fetch one job/task by id.",
        requires_id=True,
    ),
    CommandSpec(
        name="update",
        method="PATCH",
        path="/v1/tasks/{entity_id}",
        help="Patch one job/task from a JSON payload.",
        requires_id=True,
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="comment",
        method="POST",
        path="/v1/tasks/{entity_id}/comments",
        help="Attach one comment to a job/task.",
        requires_id=True,
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="reset",
        method="POST",
        path="/v1/tasks/{entity_id}/reset",
        help="Reset one DLQ job/task.",
        requires_id=True,
    ),
    CommandSpec(
        name="claim",
        method="POST",
        path="/v1/tasks/claim",
        help="Claim the next matching job/task.",
        accepts_filters=True,
    ),
    CommandSpec(
        name="release",
        method="POST",
        path="/v1/tasks/{entity_id}/release",
        help="Release one claimed job/task.",
        requires_id=True,
    ),
    CommandSpec(
        name="submit",
        method="POST",
        path="/v1/tasks/{entity_id}/submit",
        help="Submit one job/task payload.",
        requires_id=True,
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="approve",
        method="POST",
        path="/v1/tasks/{entity_id}/approve",
        help="Approve one HITL-gated job/task.",
        requires_id=True,
    ),
    CommandSpec(
        name="reject",
        method="POST",
        path="/v1/tasks/{entity_id}/reject",
        help="Reject one HITL-gated job/task.",
        requires_id=True,
        accepts_body=True,
    ),
    CommandSpec(
        name="unlock",
        method="POST",
        path="/v1/tasks/{entity_id}/escrow-unlock",
        help="Force-unlock one escrowed job/task.",
        requires_id=True,
        accepts_body=True,
    ),
)


def build_job_app():
    return build_group("Job commands.", SPECS)

