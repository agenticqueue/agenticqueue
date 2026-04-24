from __future__ import annotations

from pathlib import Path

from fastapi.routing import APIRoute

from agenticqueue_api.routers.task_types import build_task_types_router


def test_task_type_routes_live_in_dedicated_router() -> None:
    router = build_task_types_router(get_db_session=lambda: None)

    route_specs = {
        (route.path, method)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods or set()
        if method != "HEAD"
    }

    assert {
        ("/task-types", "GET"),
        ("/v1/task-types", "GET"),
        ("/task-types", "POST"),
        ("/v1/task-types", "POST"),
        ("/task-types/{task_type_name}", "GET"),
        ("/v1/task-types/{task_type_name}", "GET"),
        ("/v1/task-types/{task_type_name}", "PATCH"),
    }.issubset(route_specs)


def test_app_includes_task_type_router_without_local_task_type_decorators() -> None:
    app_source = (
        Path(__file__).resolve().parents[2]
        / "apps"
        / "api"
        / "src"
        / "agenticqueue_api"
        / "app.py"
    ).read_text(encoding="utf-8")

    assert "build_task_types_router" in app_source
    assert "app.include_router(build_task_types_router(get_db_session))" in app_source
    assert '@app.get("/v1/task-types"' not in app_source
    assert '@app.post("/v1/task-types"' not in app_source
    assert '@app.patch("/v1/task-types/{task_type_name}"' not in app_source
