"""Decision transport commands."""

from __future__ import annotations

from agenticqueue_cli.commands.factory import CommandSpec, build_group

SPECS = (
    CommandSpec(
        name="create",
        method="POST",
        path="/v1/decisions",
        help="Create one decision from a JSON payload.",
        ok_statuses=(201,),
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="list",
        method="GET",
        path="/v1/decisions",
        help="List decisions.",
        accepts_filters=True,
        supports_pagination=True,
    ),
    CommandSpec(
        name="get",
        method="GET",
        path="/v1/decisions/{entity_id}",
        help="Fetch one decision by id.",
        requires_id=True,
    ),
    CommandSpec(
        name="supersede",
        method="POST",
        path="/v1/decisions/{entity_id}/supersede",
        help="Supersede one decision from a JSON payload.",
        requires_id=True,
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="link",
        method="POST",
        path="/v1/decisions/{entity_id}/link",
        help="Link one decision to another entity from a JSON payload.",
        requires_id=True,
        accepts_body=True,
        body_required=True,
    ),
)


def build_decision_app():
    return build_group("Decision commands.", SPECS)
