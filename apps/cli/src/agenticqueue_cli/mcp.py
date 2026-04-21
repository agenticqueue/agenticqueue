"""CLI entrypoint for the AgenticQueue stdio MCP server."""

from __future__ import annotations

from agenticqueue_api.app import create_app
from agenticqueue_api.config import get_mcp_stdio_enabled


def main() -> None:
    """Run the unified MCP server over stdio."""

    if not get_mcp_stdio_enabled():
        raise SystemExit("AGENTICQUEUE_MCP_STDIO_ENABLED=false")

    app = create_app()
    app.state.mcp_server.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
