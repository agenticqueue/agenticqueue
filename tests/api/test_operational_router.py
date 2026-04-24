from __future__ import annotations

from fastapi.routing import APIRoute
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.config import get_sqlalchemy_sync_database_url


def _endpoint_module_by_path() -> dict[str, str]:
    session_factory: sessionmaker[Session] = sessionmaker(
        bind=sa.create_engine(get_sqlalchemy_sync_database_url(), future=True),
        expire_on_commit=False,
    )
    app = create_app(session_factory=session_factory)
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
    assert modules_by_path["/api/auth/bootstrap_status"] == (
        "agenticqueue_api.routers.bootstrap"
    )
    assert modules_by_path["/api/auth/bootstrap_admin"] == (
        "agenticqueue_api.routers.bootstrap"
    )
