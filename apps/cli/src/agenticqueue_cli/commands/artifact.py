"""Artifact transport commands."""

from __future__ import annotations

from agenticqueue_cli.commands.factory import CommandSpec, build_group

SPECS = (
    CommandSpec(
        name="attach",
        method="POST",
        path="/v1/artifacts",
        help="Attach one artifact from a JSON payload.",
        ok_statuses=(201,),
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="list",
        method="GET",
        path="/v1/artifacts",
        help="List artifacts.",
        accepts_filters=True,
        supports_pagination=True,
    ),
    CommandSpec(
        name="get",
        method="GET",
        path="/v1/artifacts/{entity_id}",
        help="Fetch one artifact by id.",
        requires_id=True,
    ),
)


def build_artifact_app():
    return build_group("Artifact commands.", SPECS)
