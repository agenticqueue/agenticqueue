from __future__ import annotations

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests.entities import helpers as entity_helpers

client = entity_helpers.client
clean_database = entity_helpers.clean_database
engine = entity_helpers.engine
session_factory = entity_helpers.session_factory


def _route_modules(test_client: TestClient) -> dict[str, str]:
    return {
        route.path: route.endpoint.__module__
        for route in test_client.app.routes
        if isinstance(route, APIRoute)
    }


def test_decision_mutation_routes_live_in_decisions_router(
    client: TestClient,
) -> None:
    modules = _route_modules(client)

    assert (
        modules["/v1/decisions/{decision_id}/link"]
        == "agenticqueue_api.routers.decisions"
    )
    assert (
        modules["/v1/decisions/{decision_id}/supersede"]
        == "agenticqueue_api.routers.decisions"
    )
