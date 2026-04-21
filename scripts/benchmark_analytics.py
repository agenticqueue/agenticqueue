"""Benchmark the Phase 7 analytics query paths on representative 10k-row data."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from typing import Callable

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models.actor import ActorRecord
from agenticqueue_api.models.edge import EdgeRecord
from agenticqueue_api.models.edge import EdgeRelation
from agenticqueue_api.models.packet_version import PacketVersionRecord
from agenticqueue_api.models.project import ProjectRecord
from agenticqueue_api.models.run import RunRecord
from agenticqueue_api.models.task import TaskRecord
from agenticqueue_api.models.workspace import WorkspaceRecord
from agenticqueue_api.routers.analytics import _agent_success_rates
from agenticqueue_api.routers.analytics import _blocked_heatmap
from agenticqueue_api.routers.analytics import _cycle_time_metrics
from agenticqueue_api.routers.analytics import _handoff_latency_by_actor
from agenticqueue_api.routers.analytics import _handoff_latency_histogram
from agenticqueue_api.routers.analytics import _retrieval_precision
from agenticqueue_api.routers.analytics import _review_load
from agenticqueue_api.routers.analytics import _run_durations

WINDOW_DAYS = 90
BLOCKER_COUNT = 120
PACKET_SAMPLE_COUNT = 400
COLD_HISTORY_TASK_COUNT = 90_000
LARGE_TABLE_ROW_THRESHOLD = 10_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Seed a rollback-only representative dataset, benchmark the analytics "
            "query paths, and emit a JSON report."
        )
    )
    parser.add_argument(
        "--tasks",
        type=int,
        default=10_000,
        help="Number of workload tasks to seed inside the temporary benchmark set.",
    )
    parser.add_argument(
        "--warmup-runs",
        type=int,
        default=5,
        help="Number of untimed warmup calls per measured path.",
    )
    parser.add_argument(
        "--measure-runs",
        type=int,
        default=25,
        help="Number of timed runs per measured path.",
    )
    parser.add_argument(
        "--max-query-p95-ms",
        type=float,
        default=100.0,
        help="Fail if a core query path exceeds this warm p95 budget.",
    )
    parser.add_argument(
        "--max-endpoint-p95-ms",
        type=float,
        default=250.0,
        help="Fail if GET /v1/analytics/metrics exceeds this warm p95 budget.",
    )
    parser.add_argument(
        "--historical-tasks",
        type=int,
        default=COLD_HISTORY_TASK_COUNT,
        help=(
            "Additional cold-history workload tasks outside the 90-day window so the "
            "rolling-window plans see realistic table selectivity."
        ),
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path to write the JSON report to disk.",
    )
    return parser.parse_args()


def percentile(samples: list[float], pct: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize_latencies(samples: list[float]) -> dict[str, float]:
    return {
        "p50_ms": round(percentile(samples, 0.50), 2),
        "p95_ms": round(percentile(samples, 0.95), 2),
        "max_ms": round(max(samples), 2),
    }


def collect_seq_scans(plan: dict[str, Any]) -> list[str]:
    scans: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        if node.get("Node Type") == "Seq Scan" and node.get("Relation Name"):
            scans.append(str(node["Relation Name"]))
        for child in node.get("Plans", []):
            if isinstance(child, dict):
                walk(child)

    walk(plan)
    return sorted(set(scans))


def explain_plan(
    connection: sa.Connection,
    sql: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    plan_json = connection.execute(
        sa.text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql),
        params,
    ).scalar()
    plan = plan_json[0]["Plan"]
    return {
        "root_node": plan.get("Node Type"),
        "actual_total_time_ms": round(float(plan.get("Actual Total Time", 0.0)), 3),
        "actual_rows": int(plan.get("Actual Rows", 0)),
        "seq_scans": collect_seq_scans(plan),
    }


def time_path(
    warmups: int,
    measured_runs: int,
    fn: Callable[[], object],
) -> dict[str, float]:
    for _ in range(warmups):
        fn()

    latencies: list[float] = []
    for _ in range(measured_runs):
        started = time.perf_counter()
        fn()
        latencies.append((time.perf_counter() - started) * 1000)

    return summarize_latencies(latencies)


def ts(now: dt.datetime, *, days: int = 0, hours: int = 0, minutes: int = 0) -> dt.datetime:
    return now - dt.timedelta(days=days, hours=hours, minutes=minutes)


def seed_benchmark_dataset(
    connection: sa.Connection,
    *,
    recent_task_count: int,
    historical_task_count: int,
) -> dict[str, Any]:
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    workspace_id = uuid.uuid4()
    project_id = uuid.uuid4()
    actor_ids = [uuid.uuid4() for _ in range(3)]
    admin_actor_id = actor_ids[0]

    connection.execute(
        sa.insert(WorkspaceRecord),
        [
            {
                "id": workspace_id,
                "slug": f"bench-{workspace_id.hex[:8]}",
                "name": "Analytics Benchmark Workspace",
                "description": "Rollback-only analytics benchmark workspace",
                "created_at": ts(now, days=WINDOW_DAYS),
                "updated_at": ts(now, days=WINDOW_DAYS),
            }
        ],
    )
    connection.execute(
        sa.insert(ProjectRecord),
        [
            {
                "id": project_id,
                "workspace_id": workspace_id,
                "slug": f"bench-{project_id.hex[:8]}",
                "name": "Analytics Benchmark Project",
                "description": "Rollback-only analytics benchmark project",
                "created_at": ts(now, days=WINDOW_DAYS),
                "updated_at": ts(now, days=WINDOW_DAYS),
            }
        ],
    )
    connection.execute(
        sa.insert(ActorRecord),
        [
            {
                "id": actor_ids[0],
                "handle": f"bench-admin-{actor_ids[0].hex[:8]}",
                "actor_type": "admin",
                "display_name": "Benchmark Admin",
                "auth_subject": None,
                "is_active": True,
                "created_at": ts(now, days=WINDOW_DAYS),
                "updated_at": ts(now, days=WINDOW_DAYS),
            },
            {
                "id": actor_ids[1],
                "handle": f"bench-codex-{actor_ids[1].hex[:8]}",
                "actor_type": "agent",
                "display_name": "Benchmark Codex",
                "auth_subject": None,
                "is_active": True,
                "created_at": ts(now, days=WINDOW_DAYS),
                "updated_at": ts(now, days=WINDOW_DAYS),
            },
            {
                "id": actor_ids[2],
                "handle": f"bench-gemini-{actor_ids[2].hex[:8]}",
                "actor_type": "agent",
                "display_name": "Benchmark Gemini",
                "auth_subject": None,
                "is_active": True,
                "created_at": ts(now, days=WINDOW_DAYS),
                "updated_at": ts(now, days=WINDOW_DAYS),
            },
        ],
    )

    blocker_ids = [uuid.uuid4() for _ in range(BLOCKER_COUNT)]
    task_rows: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []
    edge_rows: list[dict[str, Any]] = []
    packet_rows: list[dict[str, Any]] = []
    learning_task_ids: list[uuid.UUID] = []
    packet_task_ids: list[uuid.UUID] = []

    for index, blocker_id in enumerate(blocker_ids):
        created_at = ts(now, days=(index % 60) + 10, hours=index % 5)
        updated_at = created_at + dt.timedelta(hours=2 + (index % 6))
        task_rows.append(
            {
                "id": blocker_id,
                "project_id": project_id,
                "task_type": "coding-task",
                "title": f"Benchmark blocker {index}",
                "state": "done",
                "priority": 3,
                "labels": [],
                "description": "Seeded blocker task for analytics benchmark.",
                "contract": {
                    "surface_area": ["analytics", f"dependency-{index % 12}"],
                },
                "definition_of_done": ["done"],
                "attempt_count": 0,
                "last_failure": None,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
        learning_task_ids.append(blocker_id)

    blocked_task_count = 0
    review_queue_count = 0

    for index in range(recent_task_count + historical_task_count):
        task_id = uuid.uuid4()
        is_recent = index < recent_task_count
        age_days = (
            index % WINDOW_DAYS
            if is_recent
            else WINDOW_DAYS + 30 + (index % 180)
        )
        created_at = ts(now, days=age_days, hours=index % 24, minutes=index % 50)
        updated_at = created_at + dt.timedelta(minutes=15 + (index % 300))

        if is_recent and index % 10 == 0:
            state = "blocked"
            labels: list[str] = []
            blocked_task_count += 1
        elif is_recent and index % 13 == 0:
            state = "validated"
            labels = []
            review_queue_count += 1
        elif is_recent and index % 17 == 0:
            state = "queued"
            labels = ["needs:human-review"]
            review_queue_count += 1
        else:
            state = "done"
            labels = []

        task_rows.append(
            {
                "id": task_id,
                "project_id": project_id,
                "task_type": ["coding-task", "review-task", "ops-task"][index % 3],
                "title": f"Benchmark task {index}",
                "state": state,
                "priority": index % 5,
                "labels": labels,
                "description": "Representative analytics benchmark task.",
                "contract": {"surface_area": ["analytics", f"area-{index % 20}"]},
                "definition_of_done": ["done"],
                "attempt_count": 0,
                "last_failure": None,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )

        actor_id = actor_ids[index % len(actor_ids)]
        run_rows.append(
            {
                "id": uuid.uuid4(),
                "task_id": task_id,
                "actor_id": actor_id,
                "packet_version_id": None,
                "status": ["done", "completed", "rejected", "failed", "parked"][
                    index % 5
                ],
                "started_at": created_at,
                "ended_at": updated_at,
                "summary": "Representative analytics benchmark run.",
                "details": {"attempt": 1},
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )

        if state == "blocked":
            blocker_id = blocker_ids[index % len(blocker_ids)]
            edge_rows.append(
                {
                    "id": uuid.uuid4(),
                    "src_entity_type": "task",
                    "src_id": task_id,
                    "dst_entity_type": "task",
                    "dst_id": blocker_id,
                    "relation": EdgeRelation.GATED_BY.value,
                    "metadata": {},
                    "created_by": actor_id,
                    "created_at": created_at,
                }
            )

        if is_recent and index < PACKET_SAMPLE_COUNT:
            packet_task_ids.append(task_id)

    connection.execute(sa.insert(TaskRecord), task_rows)
    connection.execute(sa.insert(RunRecord), run_rows)
    if edge_rows:
        connection.execute(sa.insert(EdgeRecord), edge_rows)

    for index, task_id in enumerate(packet_task_ids):
        learning_a = str(learning_task_ids[index % len(learning_task_ids)])
        learning_b = str(learning_task_ids[(index + 7) % len(learning_task_ids)])
        packet_rows.append(
            {
                "id": uuid.uuid4(),
                "task_id": task_id,
                "packet_hash": f"analytics-bench-{index}-{uuid.uuid4().hex}",
                "payload": {
                    "repo_scope": {
                        "surface_area": ["analytics", f"area-{index % 20}"],
                    },
                    "relevant_learnings": [
                        {"task_id": learning_a, "title": "Representative learning A"},
                        {"task_id": learning_b, "title": "Representative learning B"},
                    ],
                },
                "created_at": ts(now, days=index % 20, minutes=index % 60),
            }
        )

    connection.execute(sa.insert(PacketVersionRecord), packet_rows)
    connection.execute(
        sa.text(
            """
            ANALYZE agenticqueue.task;
            ANALYZE agenticqueue.run;
            ANALYZE agenticqueue.edge;
            ANALYZE agenticqueue.packet_version;
            """
        )
    )

    blocked_ids = [
        row[0]
        for row in connection.execute(
            sa.text(
                """
                SELECT id
                FROM agenticqueue.task
                WHERE project_id = :project_id
                  AND state = 'blocked'
                """
            ),
            {"project_id": project_id},
        ).fetchall()
    ]

    return {
        "admin_actor_id": admin_actor_id,
        "project_id": project_id,
        "start_at": now - dt.timedelta(days=WINDOW_DAYS),
        "end_at": now,
        "blocked_ids": blocked_ids,
        "dataset": {
            "window_days": WINDOW_DAYS,
            "recent_workload_task_count": recent_task_count,
            "historical_workload_task_count": historical_task_count,
            "blocker_task_count": BLOCKER_COUNT,
            "total_task_count": recent_task_count
            + historical_task_count
            + BLOCKER_COUNT,
            "run_count": recent_task_count + historical_task_count,
            "blocked_task_count": blocked_task_count,
            "edge_count": len(edge_rows),
            "packet_version_count": len(packet_rows),
            "review_queue_count": review_queue_count,
        },
    }


def build_report(
    connection: sa.Connection,
    session: Session,
    seeded: dict[str, Any],
    *,
    warmup_runs: int,
    measure_runs: int,
) -> dict[str, Any]:
    start_at = seeded["start_at"]
    end_at = seeded["end_at"]

    run_rows = _run_durations(session, start_at=start_at)
    _handoff_latency_histogram(run_rows)
    _handoff_latency_by_actor(run_rows)
    _agent_success_rates(run_rows)

    cycle_sql = """
        SELECT
          task_type,
          COUNT(*)::int AS count,
          (
            percentile_cont(0.5) WITHIN GROUP (
              ORDER BY EXTRACT(EPOCH FROM (updated_at - created_at))
            ) / 3600.0
          )::double precision AS median_hours,
          (
            percentile_cont(0.95) WITHIN GROUP (
              ORDER BY EXTRACT(EPOCH FROM (updated_at - created_at))
            ) / 3600.0
          )::double precision AS p95_hours
        FROM agenticqueue.task
        WHERE state = 'done'
          AND updated_at >= :start_at
        GROUP BY task_type
        ORDER BY count DESC, task_type ASC
    """
    blocked_task_sql = """
        SELECT id, sequence, title, updated_at
        FROM agenticqueue.task
        WHERE state IN ('blocked', 'parked')
          AND updated_at >= :start_at
        ORDER BY updated_at DESC, id DESC
    """
    blocked_edge_sql = """
        SELECT src_id, dst_id, relation, metadata, created_at, id
        FROM agenticqueue.edge
        WHERE src_entity_type = 'task'
          AND dst_entity_type = 'task'
          AND src_id = ANY(:blocked_ids)
          AND relation IN ('depends_on', 'gated_by')
        ORDER BY created_at ASC, id ASC
    """
    handoff_sql = """
        SELECT
          COALESCE(actor.handle, 'system') AS actor,
          run.status AS status,
          EXTRACT(EPOCH FROM (run.ended_at - run.started_at)) / 60.0 AS duration_minutes
        FROM agenticqueue.run AS run
        LEFT JOIN agenticqueue.actor AS actor
          ON actor.id = run.actor_id
        WHERE run.started_at IS NOT NULL
          AND run.ended_at IS NOT NULL
          AND run.ended_at >= :start_at
    """

    plan_summaries = {
        "cycle_time": explain_plan(connection, cycle_sql, {"start_at": start_at}),
        "blocked_task_lookup": explain_plan(
            connection,
            blocked_task_sql,
            {"start_at": start_at},
        ),
        "blocked_edge_lookup": explain_plan(
            connection,
            blocked_edge_sql,
            {"blocked_ids": seeded["blocked_ids"]},
        ),
        "handoff_run_scan": explain_plan(
            connection,
            handoff_sql,
            {"start_at": start_at},
        ),
    }

    SessionFactory = sessionmaker(bind=connection, expire_on_commit=False)
    app = create_app(session_factory=SessionFactory)
    token_session = Session(bind=connection)
    _, raw_token = issue_api_token(
        token_session,
        actor_id=seeded["admin_actor_id"],
        scopes=["admin"],
        expires_at=None,
    )
    token_session.flush()

    headers = {
        "Authorization": f"Bearer {raw_token}",
        "Idempotency-Key": str(uuid.uuid4()),
    }

    with TestClient(app) as client:
        def fetch_analytics() -> object:
            response = client.get("/v1/analytics/metrics?window=90d", headers=headers)
            if response.status_code != 200:
                raise RuntimeError(
                    f"GET /v1/analytics/metrics returned {response.status_code}"
                )
            return response

        endpoint_summary = time_path(
            warmups=warmup_runs,
            measured_runs=measure_runs,
            fn=fetch_analytics,
        )

    latency_summaries = {
        "cycle_time": time_path(
            warmups=warmup_runs,
            measured_runs=measure_runs,
            fn=lambda: _cycle_time_metrics(session, start_at=start_at),
        ),
        "blocked_heatmap": time_path(
            warmups=warmup_runs,
            measured_runs=measure_runs,
            fn=lambda: _blocked_heatmap(
                session,
                start_at=start_at,
                end_at=end_at,
            ),
        ),
        "handoff_metrics": time_path(
            warmups=warmup_runs,
            measured_runs=measure_runs,
            fn=lambda: (
                lambda rows: (
                    _handoff_latency_histogram(rows),
                    _handoff_latency_by_actor(rows),
                    _agent_success_rates(rows),
                )
            )(_run_durations(session, start_at=start_at)),
        ),
        "analytics_endpoint": endpoint_summary,
    }

    return {
        "captured_at": dt.datetime.now(dt.UTC).isoformat(),
        "dataset": seeded["dataset"],
        "plans": plan_summaries,
        "latencies_ms": latency_summaries,
        "notes": {
            "window": "90d",
            "warmup_runs": warmup_runs,
            "measured_runs": measure_runs,
            "approach": (
                "Seeds representative data inside a single transaction, measures warm "
                "latency against the live analytics code paths, then rolls the entire "
                "dataset back."
            ),
        },
    }


def validate_report(
    report: dict[str, Any],
    *,
    max_query_p95_ms: float,
    max_endpoint_p95_ms: float,
) -> list[str]:
    failures: list[str] = []
    table_row_counts = {
        "task": int(report["dataset"]["total_task_count"]),
        "run": int(report["dataset"]["run_count"]),
        "edge": int(report["dataset"]["edge_count"]),
        "packet_version": int(report["dataset"]["packet_version_count"]),
    }
    large_tables = {
        name
        for name, row_count in table_row_counts.items()
        if row_count >= LARGE_TABLE_ROW_THRESHOLD
    }

    for name, plan in report["plans"].items():
        seq_scans = sorted(set(plan["seq_scans"]) & large_tables)
        if seq_scans:
            failures.append(
                f"{name} uses sequential scans on large tables: {', '.join(seq_scans)}"
            )

    for name in ("cycle_time", "blocked_heatmap", "handoff_metrics"):
        p95 = float(report["latencies_ms"][name]["p95_ms"])
        if p95 > max_query_p95_ms:
            failures.append(
                f"{name} p95 {p95:.2f}ms exceeds {max_query_p95_ms:.2f}ms budget"
            )

    endpoint_p95 = float(report["latencies_ms"]["analytics_endpoint"]["p95_ms"])
    if endpoint_p95 > max_endpoint_p95_ms:
        failures.append(
            "analytics_endpoint p95 "
            f"{endpoint_p95:.2f}ms exceeds {max_endpoint_p95_ms:.2f}ms budget"
        )

    return failures


def main() -> int:
    args = parse_args()

    engine = sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    try:
        seeded = seed_benchmark_dataset(
            connection,
            recent_task_count=args.tasks,
            historical_task_count=args.historical_tasks,
        )
        report = build_report(
            connection,
            session,
            seeded,
            warmup_runs=args.warmup_runs,
            measure_runs=args.measure_runs,
        )
        failures = validate_report(
            report,
            max_query_p95_ms=args.max_query_p95_ms,
            max_endpoint_p95_ms=args.max_endpoint_p95_ms,
        )
        report["thresholds"] = {
            "max_query_p95_ms": args.max_query_p95_ms,
            "max_endpoint_p95_ms": args.max_endpoint_p95_ms,
        }
        report["status"] = "pass" if not failures else "fail"
        report["failures"] = failures

        rendered = json.dumps(report, indent=2)
        print(rendered)

        if args.output_json is not None:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(rendered + "\n", encoding="utf-8")

        return 0 if not failures else 1
    finally:
        transaction.rollback()
        session.close()
        connection.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
