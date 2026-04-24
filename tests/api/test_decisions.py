from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from tests.entities import helpers as entity_helpers

clean_database = entity_helpers.clean_database
engine = entity_helpers.engine
session_factory = entity_helpers.session_factory


def _route_modules(app: FastAPI) -> dict[str, str]:
    return {
        route.path: route.endpoint.__module__
        for route in app.routes
        if isinstance(route, APIRoute)
    }


def test_decision_mutation_routes_live_in_decisions_router(
    session_factory: sessionmaker[Session],
) -> None:
    modules = _route_modules(create_app(session_factory=session_factory))

    assert (
        modules["/v1/decisions/{decision_id}/link"]
        == "agenticqueue_api.routers.decisions"
    )
    assert (
        modules["/v1/decisions/{decision_id}/supersede"]
        == "agenticqueue_api.routers.decisions"
    )
