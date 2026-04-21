from __future__ import annotations

from fastapi.testclient import TestClient

from tests.entities import helpers as entity_helpers

engine = entity_helpers.engine
clean_database = entity_helpers.clean_database
session_factory = entity_helpers.session_factory
client = entity_helpers.client


def test_health_endpoint_is_public_and_reports_version(client: TestClient) -> None:
    response = client.get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": "0.1.0",
    }
