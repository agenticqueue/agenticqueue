"""Policy-pack transport commands."""

from __future__ import annotations

from agenticqueue_cli.commands.factory import CommandSpec, build_group

SPECS = (
    CommandSpec(
        name="load",
        method="POST",
        path="/v1/policies",
        help="Load one policy pack from a JSON payload.",
        ok_statuses=(201,),
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="list",
        method="GET",
        path="/v1/policies",
        help="List policy packs.",
        accepts_filters=True,
        supports_pagination=True,
    ),
    CommandSpec(
        name="get",
        method="GET",
        path="/v1/policies/{entity_id}",
        help="Fetch one policy pack by id.",
        requires_id=True,
    ),
    CommandSpec(
        name="attach",
        method="PATCH",
        path="/v1/projects/{entity_id}",
        help="Attach one policy pack to a pipeline via JSON payload.",
        requires_id=True,
        accepts_body=True,
        body_required=True,
    ),
)


def build_policy_app():
    return build_group("Policy commands.", SPECS)
