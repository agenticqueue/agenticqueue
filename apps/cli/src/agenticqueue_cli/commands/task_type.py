"""Task-type registry commands."""

from __future__ import annotations

from agenticqueue_cli.commands.factory import CommandSpec, build_group

SPECS = (
    CommandSpec(
        name="register",
        method="POST",
        path="/v1/task-types",
        help="Register one task type from a JSON payload.",
        ok_statuses=(201,),
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="list",
        method="GET",
        path="/v1/task-types",
        help="List registered task types.",
        accepts_filters=True,
        supports_pagination=True,
    ),
    CommandSpec(
        name="get",
        method="GET",
        path="/v1/task-types/{entity_id}",
        help="Fetch one task type by name.",
        requires_id=True,
    ),
    CommandSpec(
        name="update",
        method="PATCH",
        path="/v1/task-types/{entity_id}",
        help="Patch one task type from a JSON payload.",
        requires_id=True,
        accepts_body=True,
        body_required=True,
    ),
)


def build_task_type_app():
    return build_group("Task-type commands.", SPECS)
