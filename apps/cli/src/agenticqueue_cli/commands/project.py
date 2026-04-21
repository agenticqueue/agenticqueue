"""Project commands backed by the workspace REST surface."""

from __future__ import annotations

from agenticqueue_cli.commands.factory import CommandSpec, build_group

SPECS = (
    CommandSpec(
        name="create",
        method="POST",
        path="/v1/workspaces",
        help="Create one project/workspace from a JSON payload.",
        ok_statuses=(201,),
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="list",
        method="GET",
        path="/v1/workspaces",
        help="List projects/workspaces.",
        accepts_filters=True,
        supports_pagination=True,
    ),
    CommandSpec(
        name="get",
        method="GET",
        path="/v1/workspaces/{entity_id}",
        help="Fetch one project/workspace by id.",
        requires_id=True,
    ),
    CommandSpec(
        name="update",
        method="PATCH",
        path="/v1/workspaces/{entity_id}",
        help="Patch one project/workspace from a JSON payload.",
        requires_id=True,
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="archive",
        method="DELETE",
        path="/v1/workspaces/{entity_id}",
        help="Archive one project/workspace by id.",
        requires_id=True,
        ok_statuses=(200, 204),
    ),
)


def build_project_app():
    return build_group("Project commands.", SPECS)
