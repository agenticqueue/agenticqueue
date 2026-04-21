from __future__ import annotations

import uuid

import psycopg
import sqlalchemy as sa
from fastapi import Depends, FastAPI
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app, get_db_session
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.config import get_sync_database_url
from agenticqueue_api.db import timeout_ms
from agenticqueue_api.models import ActorModel
from agenticqueue_api.models.edge import EdgeModel, EdgeRelation
from agenticqueue_api.repo import create_actor, create_edge

TRUNCATE_TABLES = [
    "api_token",
    "capability_grant",
    "idempotency_key",
    "edge",
    "artifact",
    "decision",
    "run",
    "packet_version",
    "learning",
    "task",
    "project",
    "policy",
    "capability",
    "audit_log",
    "workspace",
    "actor",
]
ROOT_TASK_ID = uuid.UUID("00000000-0000-0000-0000-00000000a100")
CHAIN_TASK_IDS = [
    uuid.UUID("00000000-0000-0000-0000-00000000a101"),
    uuid.UUID("00000000-0000-0000-0000-00000000a102"),
    uuid.UUID("00000000-0000-0000-0000-00000000a103"),
]
TIMEOUT_PROBE_SQL = """
WITH RECURSIVE walk(depth, entity_id) AS (
  SELECT 1, edge.dst_id
  FROM agenticqueue.edge AS edge
  WHERE edge.src_entity_type = 'task'
    AND edge.src_id = :root_id
    AND edge.dst_entity_type = 'task'
    AND edge.relation = 'depends_on'
  UNION ALL
  SELECT walk.depth + 1, edge.dst_id
  FROM walk
  JOIN LATERAL (SELECT pg_sleep(:sleep_seconds)) AS sleeper ON true
  JOIN agenticqueue.edge AS edge
    ON edge.src_entity_type = 'task'
   AND edge.src_id = walk.entity_id
   AND edge.dst_entity_type = 'task'
   AND edge.relation = 'depends_on'
  WHERE walk.depth < :max_depth
)
SELECT count(*) FROM walk
"""


def truncate_all_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        qualified_tables = ", ".join(
            f"agenticqueue.{table_name}" for table_name in TRUNCATE_TABLES
        )
        connection.execute(
            sa.text(f"TRUNCATE TABLE {qualified_tables} RESTART IDENTITY CASCADE")
        )


def seed_graph_chain(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        current_id = ROOT_TASK_ID
        for index, next_id in enumerate(CHAIN_TASK_IDS, start=1):
            create_edge(
                session,
                EdgeModel.model_validate(
                    {
                        "id": str(uuid.UUID(f"00000000-0000-0000-0000-{index:012d}")),
                        "src_entity_type": "task",
                        "src_id": str(current_id),
                        "dst_entity_type": "task",
                        "dst_id": str(next_id),
                        "relation": EdgeRelation.DEPENDS_ON.value,
                        "metadata": {},
                        "created_by": None,
                        "created_at": "2026-04-20T00:00:00+00:00",
                    }
                ),
            )
            current_id = next_id
        session.commit()


def seed_bearer_token(session_factory: sessionmaker[Session]) -> str:
    with session_factory() as session:
        actor = create_actor(
            session,
            ActorModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "handle": "timeout-probe",
                    "actor_type": "admin",
                    "display_name": "Timeout Probe",
                    "auth_subject": "timeout-probe-subject",
                    "is_active": True,
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        _, raw_token = issue_api_token(
            session,
            actor_id=actor.id,
            scopes=["admin", "task:read", "task:write"],
            expires_at=None,
        )
        session.commit()
        return raw_token


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def build_timeout_probe_app(
    session_factory: sessionmaker[Session],
    *,
    statement_timeout_ms: int,
    sleep_seconds: float = 0.05,
    endpoint_label: str = "v1.tests.graph-timeout",
) -> FastAPI:
    app = create_app(session_factory=session_factory)

    @app.get("/tests/graph-timeout", include_in_schema=False)
    def graph_timeout_probe(
        session: Session = Depends(get_db_session),
    ) -> dict[str, int]:
        with timeout_ms(
            session,
            statement_timeout_ms,
            endpoint=endpoint_label,
        ):
            count = session.execute(
                sa.text(TIMEOUT_PROBE_SQL),
                {
                    "root_id": ROOT_TASK_ID,
                    "sleep_seconds": sleep_seconds,
                    "max_depth": len(CHAIN_TASK_IDS),
                },
            ).scalar_one()
        return {"count": int(count)}

    @app.get("/tests/db-health", include_in_schema=False)
    def db_health(session: Session = Depends(get_db_session)) -> dict[str, int]:
        ok = session.execute(sa.text("SELECT 1")).scalar_one()
        return {"ok": int(ok)}

    return app


def count_backend_connections() -> int:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*)
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND usename = current_user
                  AND pid <> pg_backend_pid()
                """)
            row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def role_timeout_is_persisted() -> bool:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT EXISTS (
                  SELECT 1
                  FROM pg_roles
                  CROSS JOIN LATERAL unnest(COALESCE(rolconfig, ARRAY[]::text[])) AS config
                  WHERE rolname = current_user
                    AND config IN ('statement_timeout=5000ms', 'statement_timeout=5s')
                )
                """)
            row = cursor.fetchone()
    assert row is not None
    return bool(row[0])
