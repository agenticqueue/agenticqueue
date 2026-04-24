from __future__ import annotations

from fastapi.routing import APIRoute

from agenticqueue_api.app import create_app
from tests.entities import helpers as entity_helpers


def _endpoint_module_by_path() -> dict[str, str]:
    app = create_app(session_factory=entity_helpers.session_factory)
    return {
        route.path: route.endpoint.__module__
        for route in app.routes
        if isinstance(route, APIRoute)
    }


def test_operational_routes_live_in_dedicated_router() -> None:
    modules_by_path = _endpoint_module_by_path()

    assert modules_by_path["/healthz"] == "agenticqueue_api.routers.operational"
    assert modules_by_path["/health"] == "agenticqueue_api.routers.operational"
    assert modules_by_path["/v1/health"] == "agenticqueue_api.routers.operational"
    assert modules_by_path["/stats"] == "agenticqueue_api.routers.operational"
    assert modules_by_path["/audit/verify"] == "agenticqueue_api.routers.operational"
    assert modules_by_path["/v1/audit/verify"] == "agenticqueue_api.routers.operational"
    assert modules_by_path["/setup"] == "agenticqueue_api.app"
