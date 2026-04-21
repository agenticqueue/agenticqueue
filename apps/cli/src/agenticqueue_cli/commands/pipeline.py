"""Pipeline commands backed by the project REST surface."""

from __future__ import annotations

from agenticqueue_cli.commands.factory import CommandSpec, build_group

SPECS = (
    CommandSpec(
        name="create",
        method="POST",
        path="/v1/projects",
        help="Create one pipeline/project from a JSON payload.",
        ok_statuses=(201,),
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="list",
        method="GET",
        path="/v1/projects",
        help="List pipelines/projects.",
        accepts_filters=True,
        supports_pagination=True,
    ),
    CommandSpec(
        name="get",
        method="GET",
        path="/v1/projects/{entity_id}",
        help="Fetch one pipeline/project by id.",
        requires_id=True,
    ),
    CommandSpec(
        name="update",
        method="PATCH",
        path="/v1/projects/{entity_id}",
        help="Patch one pipeline/project from a JSON payload.",
        requires_id=True,
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="cancel",
        method="DELETE",
        path="/v1/projects/{entity_id}",
        help="Cancel one pipeline/project by id.",
        requires_id=True,
        ok_statuses=(200, 204),
    ),
)


def build_pipeline_app():
    return build_group("Pipeline commands.", SPECS)
