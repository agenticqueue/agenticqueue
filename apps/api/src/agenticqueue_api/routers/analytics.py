"""Read-only analytics aggregation surface for Phase 7."""

from __future__ import annotations

import datetime as dt
import math
import uuid
from collections import defaultdict
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from agenticqueue_api.db import graph_timeout
from agenticqueue_api.errors import raise_api_error
from agenticqueue_api.models import (
    ActorModel,
    ApiTokenModel,
    EdgeRecord,
    EdgeRelation,
    PacketVersionRecord,
    TaskRecord,
)
from agenticqueue_api.models.edge import edge_metadata_marks_superseded
from agenticqueue_api.models.shared import SchemaModel

DEFAULT_WINDOW = "90d"
MAX_PACKET_SAMPLE = 200
HANDOFF_BUCKETS: tuple[tuple[str, float, float | None], ...] = (
    ("<15m", 0.0, 15.0),
    ("15-30m", 15.0, 30.0),
    ("30-60m", 30.0, 60.0),
    ("60-120m", 60.0, 120.0),
    ("120m+", 120.0, None),
)


class AnalyticsWindowView(SchemaModel):
    """Time window metadata for one analytics response."""

    key: str
    days: int
    start_at: dt.datetime
    end_at: dt.datetime


class CycleTimeMetricView(SchemaModel):
    """Cycle time statistics for one task type."""

    task_type: str
    count: int
    median_hours: float
    p95_hours: float


class BlockedHeatmapCellView(SchemaModel):
    """Aggregated blocked-work stats for one blocker."""

    blocker_ref: str
    blocker_title: str
    task_count: int
    total_blocked_hours: float
    p95_blocked_hours: float
    sample_refs: list[str]


class HistogramBucketView(SchemaModel):
    """One histogram bucket for handoff latency."""

    label: str
    min_minutes: float
    max_minutes: float | None = None
    count: int


class ActorLatencyView(SchemaModel):
    """Per-actor latency rollup."""

    actor: str
    count: int
    median_minutes: float
    p95_minutes: float


class RetrievalPrecisionMetricView(SchemaModel):
    """Packet retrieval precision rollup."""

    sample_size: int
    precision_at_5: float
    precision_at_10: float
    note: str


class AgentSuccessMetricView(SchemaModel):
    """Per-actor run outcome rollup."""

    actor: str
    complete_count: int
    parked_count: int
    error_count: int
    total_count: int
    success_rate: float


class ReviewLoadPointView(SchemaModel):
    """Daily review-queue point."""

    day: dt.date
    count: int


class AnalyticsMetricsResponse(SchemaModel):
    """Phase 7 analytics response shape."""

    generated_at: dt.datetime
    window: AnalyticsWindowView
    cycle_time: list[CycleTimeMetricView]
    blocked_heatmap: list[BlockedHeatmapCellView]
    handoff_latency_histogram: list[HistogramBucketView]
    handoff_latency_by_actor: list[ActorLatencyView]
    retrieval_precision: RetrievalPrecisionMetricView
    agent_success_rates: list[AgentSuccessMetricView]
    review_load: list[ReviewLoadPointView]


def _require_request_auth(request: Request) -> ActorModel:
    actor = getattr(request.state, "actor", None)
    api_token = getattr(request.state, "api_token", None)
    if not isinstance(actor, ActorModel) or not isinstance(api_token, ApiTokenModel):
        raise_api_error(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")
    return actor


def _parse_window(window: str) -> int:
    normalized = window.strip().lower()
    if not normalized.endswith("d"):
        raise_api_error(
            status.HTTP_400_BAD_REQUEST,
            "window must be expressed as '<days>d'",
            details={"window": window},
        )

    try:
        days = int(normalized[:-1])
    except ValueError:
        raise_api_error(
            status.HTTP_400_BAD_REQUEST,
            "window must be expressed as '<days>d'",
            details={"window": window},
        )

    if days < 1 or days > 365:
        raise_api_error(
            status.HTTP_400_BAD_REQUEST,
            "window days must be between 1 and 365",
            details={"window": window},
        )

    return days


def _round_metric(value: float) -> float:
    return round(value, 2)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0

    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * percentile
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    lower_value = ordered[lower_index]
    upper_value = ordered[upper_index]
    if lower_index == upper_index:
        return lower_value
    fraction = position - lower_index
    return lower_value + (upper_value - lower_value) * fraction


def _task_ref(sequence: int | None, entity_id: uuid.UUID) -> str:
    return f"job-{sequence}" if sequence is not None else f"job-{str(entity_id)[:8]}"


def _normalize_surface_area(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item.strip() for item in value if isinstance(item, str) and item.strip()}


def _cycle_time_metrics(
    session: Session,
    *,
    start_at: dt.datetime,
) -> list[CycleTimeMetricView]:
    rows = session.execute(
        sa.text("""
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
            """),
        {"start_at": start_at},
    ).mappings()

    return [
        CycleTimeMetricView(
            task_type=str(row["task_type"]),
            count=int(row["count"]),
            median_hours=_round_metric(float(row["median_hours"] or 0.0)),
            p95_hours=_round_metric(float(row["p95_hours"] or 0.0)),
        )
        for row in rows
    ]


def _blocked_heatmap(
    session: Session,
    *,
    start_at: dt.datetime,
    end_at: dt.datetime,
) -> list[BlockedHeatmapCellView]:
    blocked_task_rows = session.execute(
        sa.select(
            TaskRecord.id,
            TaskRecord.sequence,
            TaskRecord.updated_at,
        )
        .where(
            TaskRecord.state.in_(("blocked", "parked")),
            TaskRecord.updated_at >= start_at,
        )
        .order_by(TaskRecord.updated_at.desc(), TaskRecord.id.desc())
    ).all()
    if not blocked_task_rows:
        return []

    blocked_ids = [row.id for row in blocked_task_rows]
    edge_rows = session.execute(
        sa.select(
            EdgeRecord.src_id,
            EdgeRecord.dst_id,
            EdgeRecord.edge_metadata,
        )
        .where(
            EdgeRecord.src_entity_type == "task",
            EdgeRecord.dst_entity_type == "task",
            EdgeRecord.src_id.in_(blocked_ids),
            EdgeRecord.relation.in_((EdgeRelation.DEPENDS_ON, EdgeRelation.GATED_BY)),
        )
        .order_by(EdgeRecord.created_at.asc(), EdgeRecord.id.asc())
    ).all()

    active_edges = [
        edge
        for edge in edge_rows
        if not edge_metadata_marks_superseded(edge.edge_metadata)
    ]
    edges_by_blocked_id: dict[uuid.UUID, list[Any]] = defaultdict(list)
    for edge in active_edges:
        edges_by_blocked_id[edge.src_id].append(edge)
    dependency_ids = sorted({edge.dst_id for edge in active_edges})
    dependency_by_id = {
        row.id: row
        for row in session.execute(
            sa.select(
                TaskRecord.id,
                TaskRecord.sequence,
                TaskRecord.title,
            ).where(TaskRecord.id.in_(dependency_ids))
        ).all()
    }

    aggregate: dict[str, dict[str, Any]] = {}

    for blocked_task in blocked_task_rows:
        blocked_hours = max(
            (end_at - blocked_task.updated_at).total_seconds() / 3600.0,
            0.0,
        )
        blocked_ref = _task_ref(blocked_task.sequence, blocked_task.id)
        task_edges = edges_by_blocked_id.get(blocked_task.id, [])
        if not task_edges:
            task_edges = []

        if not task_edges:
            blocker_key = "unresolved"
            bucket = aggregate.setdefault(
                blocker_key,
                {
                    "blocker_ref": blocker_key,
                    "blocker_title": "No active dependency edge recorded",
                    "hours": [],
                    "samples": [],
                },
            )
            bucket["hours"].append(blocked_hours)
            bucket["samples"].append(blocked_ref)
            continue

        for edge in task_edges:
            blocker = dependency_by_id.get(edge.dst_id)
            blocker_ref = (
                _task_ref(blocker.sequence, blocker.id)
                if blocker is not None
                else f"job-{str(edge.dst_id)[:8]}"
            )
            blocker_title = (
                blocker.title if blocker is not None else "Missing dependency task"
            )
            bucket = aggregate.setdefault(
                blocker_ref,
                {
                    "blocker_ref": blocker_ref,
                    "blocker_title": blocker_title,
                    "hours": [],
                    "samples": [],
                },
            )
            bucket["hours"].append(blocked_hours)
            bucket["samples"].append(blocked_ref)

    rows: list[BlockedHeatmapCellView] = []
    for bucket in aggregate.values():
        hours = [float(value) for value in bucket["hours"]]
        sample_refs = sorted(set(bucket["samples"]))[:5]
        rows.append(
            BlockedHeatmapCellView(
                blocker_ref=str(bucket["blocker_ref"]),
                blocker_title=str(bucket["blocker_title"]),
                task_count=len(hours),
                total_blocked_hours=_round_metric(sum(hours)),
                p95_blocked_hours=_round_metric(_percentile(hours, 0.95)),
                sample_refs=sample_refs,
            )
        )

    rows.sort(
        key=lambda row: (-row.total_blocked_hours, -row.task_count, row.blocker_ref)
    )
    return rows


def _run_durations(
    session: Session,
    *,
    start_at: dt.datetime,
) -> list[dict[str, Any]]:
    rows = session.execute(
        sa.text("""
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
            """),
        {"start_at": start_at},
    ).mappings()

    return [dict(row) for row in rows]


def _handoff_latency_histogram(
    run_rows: list[dict[str, Any]],
) -> list[HistogramBucketView]:
    durations = [float(row["duration_minutes"]) for row in run_rows]
    buckets: list[HistogramBucketView] = []

    for label, minimum, maximum in HANDOFF_BUCKETS:
        count = sum(
            1
            for duration in durations
            if duration >= minimum and (maximum is None or duration < maximum)
        )
        buckets.append(
            HistogramBucketView(
                label=label,
                min_minutes=minimum,
                max_minutes=maximum,
                count=count,
            )
        )

    return buckets


def _handoff_latency_by_actor(run_rows: list[dict[str, Any]]) -> list[ActorLatencyView]:
    durations_by_actor: dict[str, list[float]] = defaultdict(list)
    for row in run_rows:
        durations_by_actor[str(row["actor"])].append(float(row["duration_minutes"]))

    rows = [
        ActorLatencyView(
            actor=actor,
            count=len(durations),
            median_minutes=_round_metric(_percentile(durations, 0.5)),
            p95_minutes=_round_metric(_percentile(durations, 0.95)),
        )
        for actor, durations in durations_by_actor.items()
    ]
    rows.sort(key=lambda row: (-row.count, row.actor))
    return rows


def _success_bucket(status_value: str) -> str:
    normalized = status_value.strip().lower()

    if normalized in {"done", "completed", "complete", "succeeded", "ok"}:
        return "complete"
    if normalized in {"rejected", "dlq", "error", "failed", "test_failed"}:
        return "error"
    return "parked"


def _agent_success_rates(
    run_rows: list[dict[str, Any]],
) -> list[AgentSuccessMetricView]:
    aggregates: dict[str, dict[str, int]] = defaultdict(
        lambda: {"complete": 0, "parked": 0, "error": 0}
    )
    for row in run_rows:
        actor = str(row["actor"])
        aggregates[actor][_success_bucket(str(row["status"]))] += 1

    metrics = []
    for actor, counts in aggregates.items():
        total = counts["complete"] + counts["parked"] + counts["error"]
        success_rate = counts["complete"] / total if total > 0 else 0.0
        metrics.append(
            AgentSuccessMetricView(
                actor=actor,
                complete_count=counts["complete"],
                parked_count=counts["parked"],
                error_count=counts["error"],
                total_count=total,
                success_rate=_round_metric(success_rate),
            )
        )

    metrics.sort(key=lambda metric: (-metric.total_count, metric.actor))
    return metrics


def _review_load(
    session: Session,
    *,
    start_at: dt.datetime,
    end_at: dt.datetime,
    days: int,
) -> list[ReviewLoadPointView]:
    rows = session.execute(
        sa.text("""
            SELECT
              DATE_TRUNC('day', updated_at)::date AS day,
              COUNT(*)::int AS count
            FROM agenticqueue.task
            WHERE updated_at >= :start_at
              AND (
                state IN ('validated', 'needs_ghost_triage')
                OR 'needs:human-review' = ANY(labels)
              )
            GROUP BY DATE_TRUNC('day', updated_at)::date
            ORDER BY day ASC
            """),
        {"start_at": start_at},
    ).mappings()
    count_by_day = {cast_day["day"]: int(cast_day["count"]) for cast_day in rows}

    end_date = end_at.date()
    start_date = end_date - dt.timedelta(days=days - 1)

    return [
        ReviewLoadPointView(
            day=day,
            count=count_by_day.get(day, 0),
        )
        for day in (start_date + dt.timedelta(days=offset) for offset in range(days))
    ]


def _packet_payloads(
    session: Session,
    *,
    start_at: dt.datetime,
) -> list[dict[str, Any]]:
    rows = session.scalars(
        sa.select(PacketVersionRecord.payload)
        .where(PacketVersionRecord.created_at >= start_at)
        .order_by(PacketVersionRecord.created_at.desc(), PacketVersionRecord.id.desc())
        .limit(MAX_PACKET_SAMPLE)
    ).all()
    return [dict(payload) for payload in rows if isinstance(payload, dict)]


def _packet_surface_area(payload: dict[str, Any]) -> set[str]:
    repo_scope = payload.get("repo_scope")
    if isinstance(repo_scope, dict):
        surface_area = _normalize_surface_area(repo_scope.get("surface_area"))
        if surface_area:
            return surface_area

    task_payload = payload.get("task")
    if isinstance(task_payload, dict):
        contract = task_payload.get("contract")
        if isinstance(contract, dict):
            return _normalize_surface_area(contract.get("surface_area"))

    contract = payload.get("task_contract")
    if isinstance(contract, dict):
        return _normalize_surface_area(contract.get("surface_area"))

    return set()


def _learning_task_ids(packet_payloads: list[dict[str, Any]]) -> set[uuid.UUID]:
    task_ids: set[uuid.UUID] = set()
    for payload in packet_payloads:
        learnings = payload.get("relevant_learnings")
        if not isinstance(learnings, list):
            continue
        for learning in learnings:
            if not isinstance(learning, dict):
                continue
            raw_task_id = learning.get("task_id")
            if not isinstance(raw_task_id, str):
                continue
            try:
                task_ids.add(uuid.UUID(raw_task_id))
            except ValueError:
                continue
    return task_ids


def _learning_surfaces(
    session: Session,
    *,
    task_ids: set[uuid.UUID],
) -> dict[uuid.UUID, set[str]]:
    if not task_ids:
        return {}

    rows = session.execute(
        sa.select(TaskRecord.id, TaskRecord.contract).where(TaskRecord.id.in_(task_ids))
    ).all()
    result: dict[uuid.UUID, set[str]] = {}
    for task_id, contract in rows:
        if not isinstance(contract, dict):
            result[task_id] = set()
            continue
        result[task_id] = _normalize_surface_area(contract.get("surface_area"))
    return result


def _precision_for_packet(
    learnings: list[dict[str, Any]],
    *,
    packet_surface: set[str],
    learning_surfaces: dict[uuid.UUID, set[str]],
    k: int,
) -> float:
    candidates = [learning for learning in learnings if isinstance(learning, dict)][:k]
    if not candidates or not packet_surface:
        return 0.0

    comparable = 0
    hits = 0
    for learning in candidates:
        raw_task_id = learning.get("task_id")
        if not isinstance(raw_task_id, str):
            continue
        try:
            learning_task_id = uuid.UUID(raw_task_id)
        except ValueError:
            continue
        comparable += 1
        if packet_surface & learning_surfaces.get(learning_task_id, set()):
            hits += 1

    if comparable == 0:
        return 0.0
    return hits / comparable


def _retrieval_precision(
    session: Session,
    *,
    start_at: dt.datetime,
) -> RetrievalPrecisionMetricView:
    packet_payloads = _packet_payloads(session, start_at=start_at)
    learning_surfaces = _learning_surfaces(
        session,
        task_ids=_learning_task_ids(packet_payloads),
    )

    precision_at_5: list[float] = []
    precision_at_10: list[float] = []

    for payload in packet_payloads:
        packet_surface = _packet_surface_area(payload)
        learnings = payload.get("relevant_learnings")
        if not isinstance(learnings, list) or not packet_surface:
            continue

        precision_at_5.append(
            _precision_for_packet(
                [item for item in learnings if isinstance(item, dict)],
                packet_surface=packet_surface,
                learning_surfaces=learning_surfaces,
                k=5,
            )
        )
        precision_at_10.append(
            _precision_for_packet(
                [item for item in learnings if isinstance(item, dict)],
                packet_surface=packet_surface,
                learning_surfaces=learning_surfaces,
                k=10,
            )
        )

    sample_size = len(precision_at_5)
    if sample_size == 0:
        return RetrievalPrecisionMetricView(
            sample_size=0,
            precision_at_5=0.0,
            precision_at_10=0.0,
            note="No packet_version retrieval samples were recorded in the selected window.",
        )

    return RetrievalPrecisionMetricView(
        sample_size=sample_size,
        precision_at_5=_round_metric(sum(precision_at_5) / sample_size),
        precision_at_10=_round_metric(sum(precision_at_10) / sample_size),
        note="Surface-area overlap over packet_version retrieval payloads.",
    )


def build_analytics_router(get_db_session: Any) -> APIRouter:
    """Build the read-only analytics router."""

    router = APIRouter()

    @router.get(
        "/v1/analytics/metrics",
        response_model=AnalyticsMetricsResponse,
    )
    def analytics_metrics_endpoint(
        request: Request,
        window: str = Query(default=DEFAULT_WINDOW),
        session: Session = Depends(get_db_session),
    ) -> AnalyticsMetricsResponse:
        _require_request_auth(request)

        days = _parse_window(window)
        end_at = dt.datetime.now(dt.UTC)
        start_at = end_at - dt.timedelta(days=days)

        with graph_timeout(session, endpoint="v1.analytics.metrics"):
            cycle_time = _cycle_time_metrics(session, start_at=start_at)
            blocked_heatmap = _blocked_heatmap(
                session,
                start_at=start_at,
                end_at=end_at,
            )
            run_rows = _run_durations(session, start_at=start_at)
            handoff_latency_histogram = _handoff_latency_histogram(run_rows)
            handoff_latency_by_actor = _handoff_latency_by_actor(run_rows)
            retrieval_precision = _retrieval_precision(session, start_at=start_at)
            agent_success_rates = _agent_success_rates(run_rows)
            review_load = _review_load(
                session,
                start_at=start_at,
                end_at=end_at,
                days=days,
            )

        return AnalyticsMetricsResponse(
            generated_at=end_at,
            window=AnalyticsWindowView(
                key=f"{days}d",
                days=days,
                start_at=start_at,
                end_at=end_at,
            ),
            cycle_time=cycle_time,
            blocked_heatmap=blocked_heatmap,
            handoff_latency_histogram=handoff_latency_histogram,
            handoff_latency_by_actor=handoff_latency_by_actor,
            retrieval_precision=retrieval_precision,
            agent_success_rates=agent_success_rates,
            review_load=review_load,
        )

    return router


__all__ = ["AnalyticsMetricsResponse", "build_analytics_router"]
