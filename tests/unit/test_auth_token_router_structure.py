from __future__ import annotations

from pathlib import Path

from fastapi.routing import APIRoute

from agenticqueue_api.routers.auth_tokens import build_auth_tokens_router


def test_auth_token_routes_live_in_dedicated_router() -> None:
    router = build_auth_tokens_router(get_db_session=lambda: None)

    route_specs = {
        (route.path, method)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods or set()
        if method != "HEAD"
    }

    assert {
        ("/v1/auth/tokens", "GET"),
        ("/v1/auth/tokens", "POST"),
        ("/v1/actors/me/rotate-key", "POST"),
        ("/v1/auth/tokens/{token_id}/revoke", "POST"),
    }.issubset(route_specs)


def test_app_includes_auth_token_router_without_local_auth_token_decorators() -> None:
    app_source = (
        Path(__file__).resolve().parents[2]
        / "apps"
        / "api"
        / "src"
        / "agenticqueue_api"
        / "app.py"
    ).read_text(encoding="utf-8")

    assert "build_auth_tokens_router" in app_source
    assert "app.include_router(build_auth_tokens_router(get_db_session))" in app_source
    assert '@app.get("/v1/auth/tokens"' not in app_source
    assert '@app.post("/v1/auth/tokens"' not in app_source
    assert '@app.post("/v1/actors/me/rotate-key"' not in app_source
    assert '@app.post("/v1/auth/tokens/{token_id}/revoke"' not in app_source
