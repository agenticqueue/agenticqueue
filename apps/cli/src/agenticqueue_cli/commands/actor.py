"""Actor and identity transport commands."""

from __future__ import annotations

from agenticqueue_cli.commands.factory import CommandSpec, build_group

SPECS = (
    CommandSpec(
        name="create",
        method="POST",
        path="/v1/actors",
        help="Create one actor from a JSON payload.",
        ok_statuses=(201,),
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="list",
        method="GET",
        path="/v1/actors",
        help="List actors.",
        accepts_filters=True,
        supports_pagination=True,
    ),
    CommandSpec(
        name="revoke",
        method="DELETE",
        path="/v1/actors/{entity_id}",
        help="Revoke one actor by id.",
        requires_id=True,
        ok_statuses=(200, 204),
    ),
    CommandSpec(
        name="grant",
        method="POST",
        path="/v1/capabilities/grant",
        help="Grant one capability from a JSON payload.",
        ok_statuses=(201,),
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="revoke-cap",
        method="POST",
        path="/v1/capabilities/revoke",
        help="Revoke one capability grant from a JSON payload.",
        accepts_body=True,
        body_required=True,
    ),
)


def build_actor_app():
    return build_group("Actor / identity commands.", SPECS)
