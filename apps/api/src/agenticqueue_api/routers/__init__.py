"""Router package for focused AgenticQueue API surfaces."""

from agenticqueue_api.routers.analytics import build_analytics_router
from agenticqueue_api.routers.audit import build_audit_router
from agenticqueue_api.routers.graph import build_graph_router
from agenticqueue_api.routers.learnings import build_learnings_router
from agenticqueue_api.routers.memory import build_memory_router
from agenticqueue_api.routers.packets import build_packets_router

__all__ = [
    "build_analytics_router",
    "build_audit_router",
    "build_graph_router",
    "build_learnings_router",
    "build_memory_router",
    "build_packets_router",
]
