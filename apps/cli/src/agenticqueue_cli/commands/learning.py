"""Learning transport commands."""

from __future__ import annotations

from agenticqueue_cli.commands.factory import CommandSpec, build_group

SPECS = (
    CommandSpec(
        name="submit",
        method="POST",
        path="/v1/learnings/submit",
        help="Submit one learning payload.",
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="list",
        method="GET",
        path="/v1/learnings",
        help="List learnings.",
        accepts_filters=True,
        supports_pagination=True,
    ),
    CommandSpec(
        name="get",
        method="GET",
        path="/v1/learnings/{entity_id}",
        help="Fetch one learning by id.",
        requires_id=True,
    ),
    CommandSpec(
        name="search",
        method="GET",
        path="/v1/learnings/search",
        help="Search learnings with query-string filters.",
        accepts_filters=True,
        supports_pagination=True,
    ),
    CommandSpec(
        name="promote",
        method="POST",
        path="/v1/learnings/{entity_id}/promote",
        help="Promote one learning from a JSON payload.",
        requires_id=True,
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="supersede",
        method="POST",
        path="/v1/learnings/{entity_id}/supersede",
        help="Supersede one learning from a JSON payload.",
        requires_id=True,
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="expire",
        method="PATCH",
        path="/v1/learnings/{entity_id}",
        help="Expire one learning.",
        requires_id=True,
        accepts_body=True,
        default_body={"status": "expired"},
    ),
    CommandSpec(
        name="edit",
        method="PATCH",
        path="/v1/learnings/{entity_id}",
        help="Patch one learning from a JSON payload.",
        requires_id=True,
        accepts_body=True,
        body_required=True,
    ),
)


def build_learning_app():
    return build_group("Learning commands.", SPECS)
