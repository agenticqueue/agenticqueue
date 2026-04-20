from __future__ import annotations

import logging
from pathlib import Path
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.config import (
    get_graph_traversal_timeout_ms,
    get_role_statement_timeout_ms,
    get_sqlalchemy_sync_database_url,
    get_write_statement_timeout_ms,
)
from tests.timeout_support import (
    auth_headers,
    build_timeout_probe_app,
    count_backend_connections,
    seed_bearer_token,
    seed_graph_chain,
    truncate_all_tables,
)


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> Iterator[None]:
    truncate_all_tables(engine)
    yield


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_timeout_constants_match_contract() -> None:
    assert get_role_statement_timeout_ms() == 5000
    assert get_graph_traversal_timeout_ms() == 2000
    assert get_write_statement_timeout_ms() == 10000


def test_docker_compose_sets_server_reset_query_always() -> None:
    compose_text = (
        Path(__file__).resolve().parents[2] / "docker-compose.yml"
    ).read_text(encoding="utf-8")
    assert "SERVER_RESET_QUERY_ALWAYS: 1" in compose_text


def test_recursive_cte_timeout_returns_504_and_logs_fingerprint(
    session_factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    seed_graph_chain(session_factory)
    token = seed_bearer_token(session_factory)
    app = build_timeout_probe_app(
        session_factory,
        statement_timeout_ms=25,
        sleep_seconds=0.05,
        endpoint_label="v1.tests.graph-timeout",
    )

    with TestClient(app) as client:
        before_connections = count_backend_connections()
        caplog.set_level(logging.WARNING, logger="agenticqueue_api.db")
        response = client.get(
            "/v1/tests/graph-timeout",
            headers=auth_headers(token),
        )
        health_response = client.get(
            "/v1/tests/db-health",
            headers=auth_headers(token),
        )
        after_connections = count_backend_connections()

    assert response.status_code == 504
    assert response.json()["error_code"] == "gateway_timeout"
    assert response.json()["details"]["endpoint"] == "v1.tests.graph-timeout"
    assert response.json()["details"]["sql_fingerprint"] is not None
    assert response.json()["details"]["timeout_ms"] == 25
    assert health_response.status_code == 200
    assert health_response.json() == {"ok": 1}
    assert after_connections <= before_connections + 1
    assert any(
        "statement-timeout endpoint=v1.tests.graph-timeout" in record.getMessage()
        for record in caplog.records
    )
