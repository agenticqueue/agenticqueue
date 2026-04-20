from __future__ import annotations

import asyncio

import httpx
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from agenticqueue_api.config import (
    get_psycopg_connect_args,
    get_sqlalchemy_sync_database_url,
)
from tests.timeout_support import (
    auth_headers,
    build_timeout_probe_app,
    count_backend_connections,
    seed_bearer_token,
    seed_graph_chain,
    truncate_all_tables,
)


def _run_concurrent_requests(
    app,
    *,
    headers: dict[str, str],
    request_count: int,
    max_in_flight: int,
) -> list[int]:
    async def _run() -> list[int]:
        semaphore = asyncio.Semaphore(max_in_flight)
        transport = httpx.ASGITransport(app=app)

        async def _request(client: httpx.AsyncClient) -> int:
            async with semaphore:
                response = await client.get("/v1/tests/graph-timeout", headers=headers)
            return response.status_code

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return list(
                await asyncio.gather(*[_request(client) for _ in range(request_count)])
            )

    return asyncio.run(_run())


def test_timeout_flood_keeps_pool_healthy() -> None:
    engine = sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        future=True,
        pool_size=25,
        max_overflow=0,
        connect_args=get_psycopg_connect_args(),
    )
    truncate_all_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed_graph_chain(session_factory)
    token = seed_bearer_token(session_factory)
    app = build_timeout_probe_app(
        session_factory,
        statement_timeout_ms=25,
        sleep_seconds=0.05,
        endpoint_label="v1.tests.graph-timeout.flood",
    )
    headers = auth_headers(token)

    warm_statuses = _run_concurrent_requests(
        app,
        headers=headers,
        request_count=50,
        max_in_flight=10,
    )
    baseline_connections = count_backend_connections()
    statuses = _run_concurrent_requests(
        app,
        headers=headers,
        request_count=1000,
        max_in_flight=10,
    )
    steady_state_connections = count_backend_connections()

    try:
        assert all(status == 504 for status in warm_statuses)
        assert all(status == 504 for status in statuses)
        assert steady_state_connections <= baseline_connections + 1
    finally:
        engine.dispose()
