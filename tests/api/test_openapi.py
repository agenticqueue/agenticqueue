from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.entities import helpers as entity_helpers

engine = entity_helpers.engine
clean_database = entity_helpers.clean_database
session_factory = entity_helpers.session_factory
client = entity_helpers.client

OPENAPI_ARTIFACT_PATH = Path(__file__).resolve().parents[2] / "openapi.json"


def _load_openapi_artifact() -> dict[str, Any]:
    return json.loads(OPENAPI_ARTIFACT_PATH.read_text(encoding="utf-8"))


def test_served_openapi_matches_committed_artifact(
    client: TestClient,
    session_factory,
) -> None:
    actor = entity_helpers.seed_actor(
        session_factory,
        handle="openapi-admin",
        actor_type="admin",
        display_name="OpenAPI Admin",
    )
    token = entity_helpers.seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=["admin"],
    )

    response = client.get("/openapi.json", headers=entity_helpers.auth_headers(token))

    assert response.status_code == 200
    spec = response.json()
    committed = _load_openapi_artifact()
    assert spec["openapi"] == "3.1.0"
    assert spec["info"]["title"] == "AgenticQueue API"
    assert spec["info"]["version"] == "0.1.0"
    assert len(spec["paths"]) == len(committed["paths"])
    assert set(spec["paths"]) == set(committed["paths"])
    assert spec == committed


def test_docs_and_redoc_are_served_for_authenticated_requests(
    client: TestClient,
    session_factory,
) -> None:
    actor = entity_helpers.seed_actor(
        session_factory,
        handle="docs-admin",
        actor_type="admin",
        display_name="Docs Admin",
    )
    token = entity_helpers.seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=["admin"],
    )
    headers = entity_helpers.auth_headers(token)

    docs_response = client.get("/docs", headers=headers)
    redoc_response = client.get("/redoc", headers=headers)

    assert docs_response.status_code == 200
    assert "Swagger UI" in docs_response.text
    assert "/openapi.json" in docs_response.text
    assert redoc_response.status_code == 200
    assert "ReDoc" in redoc_response.text
    assert "/openapi.json" in redoc_response.text
