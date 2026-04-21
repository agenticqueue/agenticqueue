"""Run and audit commands."""

from __future__ import annotations

from agenticqueue_cli.commands.factory import CommandSpec, build_group

SPECS = (
    CommandSpec(
        name="list",
        method="GET",
        path="/v1/runs",
        help="List runs.",
        accepts_filters=True,
        supports_pagination=True,
    ),
    CommandSpec(
        name="get",
        method="GET",
        path="/v1/runs/{entity_id}",
        help="Fetch one run by id.",
        requires_id=True,
    ),
    CommandSpec(
        name="audit",
        method="GET",
        path="/v1/audit",
        help="Query audit rows with optional filters.",
        accepts_filters=True,
        supports_pagination=True,
    ),
)


def build_run_app():
    return build_group("Run / audit commands.", SPECS)
