"""Graph and packet commands."""

from __future__ import annotations

from agenticqueue_cli.commands.factory import CommandSpec, build_group

SPECS = (
    CommandSpec(
        name="neighborhood",
        method="GET",
        path="/v1/graph/neighborhood/{entity_id}",
        help="Query one graph neighborhood by id.",
        requires_id=True,
        accepts_filters=True,
        supports_pagination=True,
    ),
    CommandSpec(
        name="traverse",
        method="GET",
        path="/v1/graph/traverse/{entity_id}",
        help="Traverse graph edges from one entity id.",
        requires_id=True,
        accepts_filters=True,
        supports_pagination=True,
    ),
    CommandSpec(
        name="surface-search",
        method="GET",
        path="/v1/graph/surface",
        help="Search the graph by surface-area filters.",
        accepts_filters=True,
        supports_pagination=True,
    ),
    CommandSpec(
        name="packet",
        method="GET",
        path="/v1/tasks/{entity_id}/packet",
        help="Compile one context packet by task id.",
        requires_id=True,
    ),
)


def build_graph_app():
    return build_group("Graph commands.", SPECS)
