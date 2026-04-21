"""Admin and system commands."""

from __future__ import annotations

from agenticqueue_cli.commands.factory import CommandSpec, build_group

SPECS = (
    CommandSpec(
        name="health",
        method="GET",
        path="/healthz",
        help="Check server health.",
        fallback_paths=("/health",),
    ),
    CommandSpec(
        name="stats",
        method="GET",
        path="/stats",
        help="Fetch server statistics.",
    ),
    CommandSpec(
        name="setup",
        method="POST",
        path="/setup",
        help="Run first-time setup with an optional JSON payload.",
        accepts_body=True,
    ),
)


def build_admin_app():
    return build_group("Admin / system commands.", SPECS)

