from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from agenticqueue_api.mcp.learnings_tools import build_learnings_mcp
from agenticqueue_api.mcp.packet_tools import build_packets_mcp
from agenticqueue_api.mcp.server import _mounted_tool_names


def test_mounted_tool_names_reads_fastmcp_local_provider_tools() -> None:
    session_factory = sessionmaker()

    packet_names = _mounted_tool_names(
        build_packets_mcp(session_factory=session_factory)
    )
    learning_names = _mounted_tool_names(
        build_learnings_mcp(session_factory=session_factory)
    )

    assert packet_names == {"compile_packet"}
    assert {"promote_learning", "search_learnings", "supersede_learning"}.issubset(
        learning_names
    )
