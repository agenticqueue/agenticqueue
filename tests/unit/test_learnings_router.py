from __future__ import annotations

from agenticqueue_api.routers.learnings import build_learnings_router


def _unused_db_session() -> None:
    raise AssertionError("build_learnings_router should not call the DB dependency")


def test_learnings_router_exposes_learning_draft_mutation_routes() -> None:
    router = build_learnings_router(_unused_db_session)

    paths = {route.path for route in router.routes}

    assert {
        "/learnings/drafts/{draft_id}/edit",
        "/v1/learnings/drafts/{draft_id}/edit",
        "/learnings/drafts/{draft_id}/reject",
        "/v1/learnings/drafts/{draft_id}/reject",
        "/learnings/drafts/{draft_id}/confirm",
        "/v1/learnings/drafts/{draft_id}/confirm",
    }.issubset(paths)
