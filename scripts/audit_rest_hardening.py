"""Audit the public REST surface hardening matrix against docs + live OpenAPI."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import statistics
import sys
import time
import uuid
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
from typing import cast

import httpx
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(REPO_ROOT))

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.config import get_psycopg_connect_args
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import ActorModel
from agenticqueue_api.models import ArtifactModel
from agenticqueue_api.models import DecisionModel
from agenticqueue_api.models import ProjectModel
from agenticqueue_api.models import RunModel
from agenticqueue_api.models import TaskModel
from agenticqueue_api.models import WorkspaceModel
from agenticqueue_api.repo import create_actor
from agenticqueue_api.repo import create_artifact
from agenticqueue_api.repo import create_decision
from agenticqueue_api.repo import create_project
from agenticqueue_api.repo import create_run
from agenticqueue_api.repo import create_task
from agenticqueue_api.repo import create_workspace
from tests.entities.helpers import make_actor_payload
from tests.entities.helpers import make_coding_task_contract
from tests.entities.helpers import model_from
from tests.security.firewall.surface_contract import SurfaceOperation
from tests.security.firewall.surface_contract import load_surface_operations
from tests.security.firewall.surface_contract import normalize_path_template

SOAK_REQUEST_TIMEOUT_SECONDS = 5.0
SOAK_CANCEL_GRACE_SECONDS = 5.0
CI_SOAK_DURATION_SECONDS = 60
CI_SOAK_ACTOR_COUNT = 10
CI_SOAK_RPS_PER_ACTOR = 2.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the documented AgenticQueue REST hardening matrix against the "
            "live FastAPI surface and optionally run the sustained read soak."
        )
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path to write the matrix audit report.",
    )
    parser.add_argument(
        "--soak-output-json",
        type=Path,
        default=None,
        help="Optional path to write the soak report.",
    )
    parser.add_argument(
        "--soak-seconds",
        type=int,
        default=0,
        help="Run the read soak for this many seconds after the matrix audit.",
    )
    parser.add_argument(
        "--actors",
        type=int,
        default=100,
        help="Concurrent actor count for the soak.",
    )
    parser.add_argument(
        "--rps-per-actor",
        type=float,
        default=10.0,
        help="Target requests per second per actor during the soak.",
    )
    parser.add_argument(
        "--max-read-p99-ms",
        type=float,
        default=200.0,
        help="Fail the soak if the read p99 exceeds this threshold.",
    )
    return parser.parse_args()


@dataclass(frozen=True)
class AuditContext:
    admin_actor_id: uuid.UUID
    admin_token: str
    admin_token_id: uuid.UUID
    actor_id: uuid.UUID
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    task_id: uuid.UUID
    run_id: uuid.UUID
    artifact_id: uuid.UUID
    decision_id: uuid.UUID
    request_prefix: str


@dataclass(frozen=True)
class AuditSeedBundle:
    context: AuditContext
    cleanup_ids: dict[str, list[uuid.UUID]]


@dataclass(frozen=True)
class ProbeToken:
    token_id: uuid.UUID
    raw_token: str


@dataclass
class ProbeRecord:
    sequence: str
    name: str
    method: str
    path: str
    status: str
    notes: list[str] = field(default_factory=list)


@dataclass
class EnginePoolStats:
    checked_out: int = 0
    peak_checked_out: int = 0
    total_checkouts: int = 0
    total_checkins: int = 0


@dataclass(frozen=True)
class SoakConfig:
    requested_duration_seconds: int
    effective_duration_seconds: int
    requested_actor_count: int
    effective_actor_count: int
    requested_rps_per_actor: float
    effective_rps_per_actor: float
    ci_mode_enabled: bool


def _env_flag(name: str) -> bool | None:
    value = os.environ.get(name)
    if value is None:
        return None

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _resolve_soak_config(
    *,
    duration_seconds: int,
    actor_count: int,
    rps_per_actor: float,
) -> SoakConfig:
    ci_mode_enabled = _env_flag("SOAK_CI_MODE")
    if ci_mode_enabled is None:
        ci_mode_enabled = _env_flag("CI") is True

    effective_duration_seconds = duration_seconds
    effective_actor_count = actor_count
    effective_rps_per_actor = rps_per_actor

    if ci_mode_enabled:
        # Shared CI runners cannot sustain the full local soak profile.
        effective_duration_seconds = min(
            duration_seconds,
            CI_SOAK_DURATION_SECONDS,
        )
        effective_actor_count = min(
            actor_count,
            CI_SOAK_ACTOR_COUNT,
        )
        effective_rps_per_actor = min(
            rps_per_actor,
            CI_SOAK_RPS_PER_ACTOR,
        )

    return SoakConfig(
        requested_duration_seconds=duration_seconds,
        effective_duration_seconds=effective_duration_seconds,
        requested_actor_count=actor_count,
        effective_actor_count=effective_actor_count,
        requested_rps_per_actor=rps_per_actor,
        effective_rps_per_actor=effective_rps_per_actor,
        ci_mode_enabled=ci_mode_enabled,
    )


def _summarize_latency_metrics(
    latencies: list[float],
) -> tuple[float, float, str | None]:
    if not latencies:
        return 0.0, 0.0, "No successful latency samples were captured during the soak."

    p50 = (
        statistics.quantiles(latencies, n=100)[49]
        if len(latencies) >= 100
        else statistics.median(latencies)
    )
    p99 = (
        statistics.quantiles(latencies, n=100)[98]
        if len(latencies) >= 100
        else max(latencies, default=0.0)
    )
    return p50, p99, None


def _json_headers(
    *,
    token: str | None,
    request_id: str,
    include_idempotency: bool,
) -> dict[str, str]:
    headers = {"X-Request-Id": request_id}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if include_idempotency:
        headers["Idempotency-Key"] = str(uuid.uuid4())
    return headers


def _is_error_envelope(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    required = {"error_code", "message", "details", "error"}
    if not required.issubset(payload):
        return False
    error = payload["error"]
    return (
        isinstance(error, dict)
        and error.get("code") == payload.get("error_code")
        and error.get("message") == payload.get("message")
    )


def _normalize_openapi_index(
    openapi: dict[str, Any],
) -> dict[str, dict[str, dict[str, Any]]]:
    index: dict[str, dict[str, dict[str, Any]]] = {}
    for path, methods in openapi["paths"].items():
        normalized = normalize_path_template(path)
        index[normalized] = {
            method.upper(): cast(dict[str, Any], operation)
            for method, operation in methods.items()
        }
    return index


def _operation_params(operation: dict[str, Any]) -> set[str]:
    return {
        parameter["name"]
        for parameter in operation.get("parameters", [])
        if isinstance(parameter, dict)
    }


def _placeholder_path(
    operation: SurfaceOperation,
    context: AuditContext,
    *,
    token_id: uuid.UUID | None = None,
) -> str:
    path = operation.path
    replacements = {
        "{actor_id}": str(context.actor_id),
        "{task_id}": str(context.task_id),
        "{decision_id}": str(context.decision_id),
        "{token_id}": str(context.admin_token_id if token_id is None else token_id),
        "{task_type_name}": "coding-task",
        "{draft_id}": str(uuid.uuid4()),
        "{learning_id}": str(uuid.uuid4()),
    }
    for marker, value in replacements.items():
        path = path.replace(marker, value)

    if "{entity_id}" in path or "{id}" in path:
        entity_id = str(uuid.uuid4())
        if path.startswith("/v1/workspaces"):
            entity_id = str(context.workspace_id)
        elif path.startswith("/v1/projects"):
            entity_id = str(context.project_id)
        elif path.startswith("/v1/tasks"):
            entity_id = str(context.task_id)
        elif path.startswith("/v1/runs"):
            entity_id = str(context.run_id)
        elif path.startswith("/v1/artifacts"):
            entity_id = str(context.artifact_id)
        elif path.startswith("/v1/decisions"):
            entity_id = str(context.decision_id)

        path = path.replace("{entity_id}", entity_id).replace("{id}", entity_id)

    return path


def _issue_probe_token(
    session_factory: sessionmaker[Session],
    *,
    actor_id: uuid.UUID,
) -> ProbeToken:
    with session_factory() as session:
        token_model, raw_token = issue_api_token(
            session,
            actor_id=actor_id,
            scopes=["admin"],
            expires_at=None,
        )
        session.commit()
        return ProbeToken(token_id=token_model.id, raw_token=raw_token)


def _query_params_for_operation(
    operation: SurfaceOperation,
    context: AuditContext,
) -> dict[str, str]:
    params: dict[str, str] = {}
    if operation.is_list_like:
        params["limit"] = "1"

    sample_values = {
        "tag": "src/api",
        "q": "learning",
        "task_id": str(context.task_id),
        "actor": str(context.admin_actor_id),
        "since": "2026-04-20T00:00:00+00:00",
    }
    for name in operation.query_params:
        if name not in {"limit", "cursor"} and name in sample_values:
            params[name] = sample_values[name]
    return params


def _json_payload_for_operation(operation: SurfaceOperation) -> dict[str, Any] | None:
    if operation.method not in {"POST", "PATCH"}:
        return None
    return {}


def _seed_surface_context(session: Session) -> AuditSeedBundle:
    cleanup_ids: dict[str, list[uuid.UUID]] = {
        "actor_ids": [],
        "api_token_ids": [],
        "workspace_ids": [],
        "project_ids": [],
        "task_ids": [],
        "run_ids": [],
        "artifact_ids": [],
        "decision_ids": [],
    }
    admin_actor = create_actor(
        session,
        make_actor_payload(
            handle=f"aq-rest-audit-admin-{uuid.uuid4().hex[:8]}",
            actor_type="admin",
            display_name="REST Audit Admin",
        ),
    )
    cleanup_ids["actor_ids"].append(admin_actor.id)
    actor = create_actor(
        session,
        make_actor_payload(
            handle=f"aq-rest-audit-agent-{uuid.uuid4().hex[:8]}",
            actor_type="agent",
            display_name="REST Audit Agent",
        ),
    )
    cleanup_ids["actor_ids"].append(actor.id)
    token_model, raw_token = issue_api_token(
        session,
        actor_id=admin_actor.id,
        scopes=["admin"],
        expires_at=None,
    )
    cleanup_ids["api_token_ids"].append(token_model.id)

    workspace_one = create_workspace(
        session,
        model_from(
            WorkspaceModel,
            {
                "id": str(uuid.uuid4()),
                "slug": f"audit-workspace-{uuid.uuid4().hex[:8]}",
                "name": "REST Audit Workspace One",
                "description": "REST audit workspace one",
                "created_at": "2026-04-21T00:00:00+00:00",
                "updated_at": "2026-04-21T00:00:00+00:00",
            },
        ),
    )
    cleanup_ids["workspace_ids"].append(workspace_one.id)
    workspace_two = create_workspace(
        session,
        model_from(
            WorkspaceModel,
            {
                "id": str(uuid.uuid4()),
                "slug": f"audit-workspace-{uuid.uuid4().hex[:8]}",
                "name": "REST Audit Workspace Two",
                "description": "REST audit workspace two",
                "created_at": "2026-04-21T00:01:00+00:00",
                "updated_at": "2026-04-21T00:01:00+00:00",
            },
        ),
    )
    cleanup_ids["workspace_ids"].append(workspace_two.id)

    project_one = create_project(
        session,
        model_from(
            ProjectModel,
            {
                "id": str(uuid.uuid4()),
                "workspace_id": str(workspace_one.id),
                "slug": f"audit-project-{uuid.uuid4().hex[:8]}",
                "name": "REST Audit Project One",
                "description": "REST audit project one",
                "created_at": "2026-04-21T00:00:00+00:00",
                "updated_at": "2026-04-21T00:00:00+00:00",
            },
        ),
    )
    cleanup_ids["project_ids"].append(project_one.id)
    project_two = create_project(
        session,
        model_from(
            ProjectModel,
            {
                "id": str(uuid.uuid4()),
                "workspace_id": str(workspace_one.id),
                "slug": f"audit-project-{uuid.uuid4().hex[:8]}",
                "name": "REST Audit Project Two",
                "description": "REST audit project two",
                "created_at": "2026-04-21T00:01:00+00:00",
                "updated_at": "2026-04-21T00:01:00+00:00",
            },
        ),
    )
    cleanup_ids["project_ids"].append(project_two.id)

    task_one = create_task(
        session,
        model_from(
            TaskModel,
            {
                "id": str(uuid.uuid4()),
                "project_id": str(project_one.id),
                "task_type": "coding-task",
                "title": "REST Audit Task One",
                "state": "queued",
                "description": "REST audit task one",
                "contract": make_coding_task_contract(surface_area=["src/api"]),
                "definition_of_done": ["done"],
                "created_at": "2026-04-21T00:00:00+00:00",
                "updated_at": "2026-04-21T00:00:00+00:00",
            },
        ),
    )
    cleanup_ids["task_ids"].append(task_one.id)
    task_two = create_task(
        session,
        model_from(
            TaskModel,
            {
                "id": str(uuid.uuid4()),
                "project_id": str(project_one.id),
                "task_type": "coding-task",
                "title": "REST Audit Task Two",
                "state": "queued",
                "description": "REST audit task two",
                "contract": make_coding_task_contract(surface_area=["src/api/tasks"]),
                "definition_of_done": ["done"],
                "created_at": "2026-04-21T00:01:00+00:00",
                "updated_at": "2026-04-21T00:01:00+00:00",
            },
        ),
    )
    cleanup_ids["task_ids"].append(task_two.id)

    run_one = create_run(
        session,
        model_from(
            RunModel,
            {
                "id": str(uuid.uuid4()),
                "task_id": str(task_one.id),
                "actor_id": str(admin_actor.id),
                "status": "running",
                "started_at": "2026-04-21T00:00:00+00:00",
                "ended_at": None,
                "summary": "REST audit run one",
                "details": {"attempt": 1},
                "created_at": "2026-04-21T00:00:00+00:00",
                "updated_at": "2026-04-21T00:00:00+00:00",
            },
        ),
    )
    cleanup_ids["run_ids"].append(run_one.id)
    run_two = create_run(
        session,
        model_from(
            RunModel,
            {
                "id": str(uuid.uuid4()),
                "task_id": str(task_one.id),
                "actor_id": str(actor.id),
                "status": "completed",
                "started_at": "2026-04-21T00:05:00+00:00",
                "ended_at": "2026-04-21T00:06:00+00:00",
                "summary": "REST audit run two",
                "details": {"attempt": 1},
                "created_at": "2026-04-21T00:05:00+00:00",
                "updated_at": "2026-04-21T00:06:00+00:00",
            },
        ),
    )
    cleanup_ids["run_ids"].append(run_two.id)

    artifact_one = create_artifact(
        session,
        model_from(
            ArtifactModel,
            {
                "id": str(uuid.uuid4()),
                "task_id": str(task_one.id),
                "run_id": str(run_one.id),
                "kind": "patch",
                "uri": "artifacts/diffs/rest-audit.patch",
                "details": {"format": "unified-diff"},
                "embedding": None,
                "created_at": "2026-04-21T00:00:00+00:00",
                "updated_at": "2026-04-21T00:00:00+00:00",
            },
        ),
    )
    cleanup_ids["artifact_ids"].append(artifact_one.id)
    artifact_two = create_artifact(
        session,
        model_from(
            ArtifactModel,
            {
                "id": str(uuid.uuid4()),
                "task_id": str(task_one.id),
                "run_id": str(run_one.id),
                "kind": "report",
                "uri": "artifacts/reports/rest-audit.json",
                "details": {"format": "json"},
                "embedding": None,
                "created_at": "2026-04-21T00:01:00+00:00",
                "updated_at": "2026-04-21T00:01:00+00:00",
            },
        ),
    )
    cleanup_ids["artifact_ids"].append(artifact_two.id)

    decision_one = create_decision(
        session,
        model_from(
            DecisionModel,
            {
                "id": str(uuid.uuid4()),
                "task_id": str(task_one.id),
                "run_id": str(run_one.id),
                "actor_id": str(admin_actor.id),
                "summary": "REST audit decision one",
                "rationale": "Decision one rationale",
                "decided_at": "2026-04-21T00:00:00+00:00",
                "embedding": None,
                "created_at": "2026-04-21T00:00:00+00:00",
            },
        ),
    )
    cleanup_ids["decision_ids"].append(decision_one.id)
    decision_two = create_decision(
        session,
        model_from(
            DecisionModel,
            {
                "id": str(uuid.uuid4()),
                "task_id": str(task_one.id),
                "run_id": str(run_one.id),
                "actor_id": str(admin_actor.id),
                "summary": "REST audit decision two",
                "rationale": "Decision two rationale",
                "decided_at": "2026-04-21T00:01:00+00:00",
                "embedding": None,
                "created_at": "2026-04-21T00:01:00+00:00",
            },
        ),
    )
    cleanup_ids["decision_ids"].append(decision_two.id)

    return AuditSeedBundle(
        context=AuditContext(
            admin_actor_id=admin_actor.id,
            admin_token=raw_token,
            admin_token_id=token_model.id,
            actor_id=actor.id,
            workspace_id=workspace_one.id,
            project_id=project_one.id,
            task_id=task_one.id,
            run_id=run_one.id,
            artifact_id=artifact_one.id,
            decision_id=decision_one.id,
            request_prefix="rest-hardening-audit",
        ),
        cleanup_ids=cleanup_ids,
    )


def _record_failure(record: ProbeRecord, message: str) -> None:
    record.status = "fail"
    record.notes.append(message)


def _record_note(record: ProbeRecord, message: str) -> None:
    record.notes.append(message)


def _cleanup_audit_seed(
    session_factory: sessionmaker[Session],
    cleanup_ids: dict[str, list[uuid.UUID]],
    *,
    request_prefix: str,
) -> None:
    del request_prefix
    with session_factory() as session:
        if cleanup_ids["artifact_ids"]:
            session.execute(
                sa.text("DELETE FROM agenticqueue.artifact WHERE id = ANY(:ids)"),
                {"ids": cleanup_ids["artifact_ids"]},
            )
        if cleanup_ids["decision_ids"]:
            session.execute(
                sa.text("DELETE FROM agenticqueue.decision WHERE id = ANY(:ids)"),
                {"ids": cleanup_ids["decision_ids"]},
            )
        if cleanup_ids["run_ids"]:
            session.execute(
                sa.text("DELETE FROM agenticqueue.run WHERE id = ANY(:ids)"),
                {"ids": cleanup_ids["run_ids"]},
            )
        if cleanup_ids["task_ids"]:
            session.execute(
                sa.text("DELETE FROM agenticqueue.task WHERE id = ANY(:ids)"),
                {"ids": cleanup_ids["task_ids"]},
            )
        if cleanup_ids["project_ids"]:
            session.execute(
                sa.text("DELETE FROM agenticqueue.project WHERE id = ANY(:ids)"),
                {"ids": cleanup_ids["project_ids"]},
            )
        if cleanup_ids["workspace_ids"]:
            session.execute(
                sa.text("DELETE FROM agenticqueue.workspace WHERE id = ANY(:ids)"),
                {"ids": cleanup_ids["workspace_ids"]},
            )
        if cleanup_ids["api_token_ids"]:
            session.execute(
                sa.text("DELETE FROM agenticqueue.api_token WHERE id = ANY(:ids)"),
                {"ids": cleanup_ids["api_token_ids"]},
            )
        if cleanup_ids["actor_ids"]:
            session.execute(
                sa.text(
                    "DELETE FROM agenticqueue.idempotency_key WHERE actor_id = ANY(:ids)"
                ),
                {"ids": cleanup_ids["actor_ids"]},
            )
        if cleanup_ids["actor_ids"]:
            session.execute(
                sa.text("DELETE FROM agenticqueue.actor WHERE id = ANY(:ids)"),
                {"ids": cleanup_ids["actor_ids"]},
            )
        session.commit()


def run_audit() -> tuple[dict[str, Any], int]:
    previous_rate_limit_rps = os.environ.get("AGENTICQUEUE_RATE_LIMIT_RPS")
    previous_rate_limit_burst = os.environ.get("AGENTICQUEUE_RATE_LIMIT_BURST")
    os.environ["AGENTICQUEUE_RATE_LIMIT_RPS"] = "10000"
    os.environ["AGENTICQUEUE_RATE_LIMIT_BURST"] = "10000"

    engine = sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        future=True,
        connect_args=get_psycopg_connect_args(),
    )
    audit_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = audit_session_factory()
    failures = 0
    seed_bundle: AuditSeedBundle | None = None

    try:
        seed_bundle = _seed_surface_context(session)
        session.commit()
        context = seed_bundle.context

        app = create_app(session_factory=audit_session_factory)
        with TestClient(app) as client:
            openapi_response = client.get(
                "/openapi.json",
                headers=_json_headers(
                    token=context.admin_token,
                    request_id=f"{context.request_prefix}-openapi",
                    include_idempotency=False,
                ),
            )
            if openapi_response.status_code != 200:
                raise RuntimeError(
                    f"/openapi.json returned {openapi_response.status_code}"
                )
            openapi = cast(dict[str, Any], openapi_response.json())
            openapi_index = _normalize_openapi_index(openapi)

            records: list[ProbeRecord] = []
            for operation in load_surface_operations():
                record = ProbeRecord(
                    sequence=operation.sequence,
                    name=operation.name,
                    method=operation.method,
                    path=operation.path,
                    status="pass",
                )
                normalized = operation.normalized_path
                openapi_methods = openapi_index.get(normalized)
                if openapi_methods is None:
                    _record_failure(
                        record,
                        f"Documented path {operation.path} is missing from live OpenAPI.",
                    )
                    records.append(record)
                    failures += 1
                    continue

                live_operation = openapi_methods.get(operation.method)
                if live_operation is None:
                    _record_failure(
                        record,
                        f"Documented method {operation.method} {operation.path} is missing from live OpenAPI.",
                    )
                    records.append(record)
                    failures += 1
                    continue

                live_params = _operation_params(live_operation)
                missing_doc_params = sorted(set(operation.query_params) - live_params)
                if missing_doc_params:
                    _record_note(
                        record,
                        "OpenAPI omits documented query params: "
                        + ", ".join(missing_doc_params),
                    )

                probe_params = _query_params_for_operation(operation, context)
                probe_payload = _json_payload_for_operation(operation)

                if operation.is_mutation:
                    probe_token = _issue_probe_token(
                        audit_session_factory,
                        actor_id=context.admin_actor_id,
                    )
                    probe_path = _placeholder_path(
                        operation,
                        context,
                        token_id=probe_token.token_id,
                    )
                    without_idempotency = client.request(
                        operation.method,
                        probe_path,
                        params=probe_params,
                        headers=_json_headers(
                            token=probe_token.raw_token,
                            request_id=f"{context.request_prefix}-{operation.sequence}-missing-idempotency",
                            include_idempotency=False,
                        ),
                        json=probe_payload,
                    )
                    if without_idempotency.status_code != 400:
                        _record_failure(
                            record,
                            "Missing Idempotency-Key did not return HTTP 400.",
                        )
                    elif "Idempotency-Key" not in without_idempotency.text:
                        _record_failure(
                            record,
                            "Missing Idempotency-Key response did not mention the header.",
                        )
                    if "X-Request-Id" not in without_idempotency.headers:
                        _record_failure(
                            record,
                            "Missing Idempotency-Key response did not echo X-Request-Id.",
                        )
                    else:
                        payload = without_idempotency.json()
                        if not _is_error_envelope(payload):
                            _record_failure(
                                record,
                                "Missing Idempotency-Key response was not the uniform error envelope.",
                            )

                    with_idempotency = client.request(
                        operation.method,
                        probe_path,
                        params=probe_params,
                        headers=_json_headers(
                            token=probe_token.raw_token,
                            request_id=f"{context.request_prefix}-{operation.sequence}-with-idempotency",
                            include_idempotency=True,
                        ),
                        json=probe_payload,
                    )
                    if with_idempotency.status_code >= 500:
                        _record_failure(
                            record,
                            f"Mutation probe returned {with_idempotency.status_code}.",
                        )
                    if "X-Request-Id" not in with_idempotency.headers:
                        _record_failure(
                            record,
                            "Mutation probe did not echo X-Request-Id.",
                        )
                    elif with_idempotency.status_code >= 400 and not _is_error_envelope(
                        with_idempotency.json()
                    ):
                        _record_failure(
                            record,
                            "Mutation probe error did not use the uniform error envelope.",
                        )
                else:
                    probe_path = _placeholder_path(operation, context)
                    response = client.request(
                        operation.method,
                        probe_path,
                        params=probe_params,
                        headers=_json_headers(
                            token=context.admin_token,
                            request_id=f"{context.request_prefix}-{operation.sequence}-read",
                            include_idempotency=False,
                        ),
                    )
                    if response.status_code >= 500:
                        _record_failure(
                            record,
                            f"Read probe returned {response.status_code}.",
                        )
                    if (
                        operation.is_list_like or operation.query_params
                    ) and response.status_code != 200:
                        _record_failure(
                            record,
                            f"Read probe with documented query params returned {response.status_code} instead of 200.",
                        )
                    if "X-Request-Id" not in response.headers:
                        _record_failure(
                            record,
                            "Read probe did not echo X-Request-Id.",
                        )
                    if response.status_code >= 400 and not _is_error_envelope(
                        response.json()
                    ):
                        _record_failure(
                            record,
                            "Read probe error did not use the uniform error envelope.",
                        )
                    if operation.is_list_like and response.status_code == 200:
                        if response.headers.get("X-List-Limit") != "1":
                            _record_failure(
                                record,
                                "List probe did not echo X-List-Limit=1.",
                            )
                        next_cursor = response.headers.get("X-Next-Cursor")
                        if next_cursor:
                            next_page = client.request(
                                operation.method,
                                probe_path,
                                params={**probe_params, "cursor": next_cursor},
                                headers=_json_headers(
                                    token=context.admin_token,
                                    request_id=f"{context.request_prefix}-{operation.sequence}-cursor",
                                    include_idempotency=False,
                                ),
                            )
                            if next_page.status_code >= 500:
                                _record_failure(
                                    record,
                                    f"Cursor probe returned {next_page.status_code}.",
                                )
                        else:
                            _record_note(
                                record,
                                "X-Next-Cursor absent on the sampled page; pagination params still accepted.",
                            )

                if record.status == "fail":
                    failures += 1
                records.append(record)

        report = {
            "captured_at": dt.datetime.now(dt.UTC).isoformat(),
            "status": "pass" if failures == 0 else "fail",
            "surface_doc": str(REPO_ROOT / "docs" / "surface-1.0.md"),
            "openapi_source": "/openapi.json",
            "operation_count": len(records),
            "failed_operation_count": failures,
            "operations": [asdict(record) for record in records],
        }
        return report, failures
    finally:
        session.close()
        engine.dispose()
        if previous_rate_limit_rps is None:
            os.environ.pop("AGENTICQUEUE_RATE_LIMIT_RPS", None)
        else:
            os.environ["AGENTICQUEUE_RATE_LIMIT_RPS"] = previous_rate_limit_rps
        if previous_rate_limit_burst is None:
            os.environ.pop("AGENTICQUEUE_RATE_LIMIT_BURST", None)
        else:
            os.environ["AGENTICQUEUE_RATE_LIMIT_BURST"] = previous_rate_limit_burst


def _attach_pool_tracking(engine: sa.Engine) -> EnginePoolStats:
    stats = EnginePoolStats()

    @event.listens_for(engine, "checkout")
    def _checkout(  # type: ignore[no-untyped-def]
        dbapi_connection,
        connection_record,
        connection_proxy,
    ) -> None:
        del dbapi_connection, connection_record, connection_proxy
        stats.checked_out += 1
        stats.total_checkouts += 1
        stats.peak_checked_out = max(stats.peak_checked_out, stats.checked_out)

    @event.listens_for(engine, "checkin")
    def _checkin(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        del dbapi_connection, connection_record
        stats.checked_out = max(0, stats.checked_out - 1)
        stats.total_checkins += 1

    return stats


def _seed_soak_data(
    session: Session, *, actor_count: int
) -> tuple[list[str], list[uuid.UUID], uuid.UUID]:
    run_suffix = uuid.uuid4().hex[:8]
    workspace = create_workspace(
        session,
        model_from(
            WorkspaceModel,
            {
                "id": str(uuid.uuid4()),
                "slug": f"soak-workspace-{uuid.uuid4().hex[:8]}",
                "name": "REST Soak Workspace",
                "description": "REST hardening soak workspace",
                "created_at": "2026-04-21T00:00:00+00:00",
                "updated_at": "2026-04-21T00:00:00+00:00",
            },
        ),
    )
    project = create_project(
        session,
        model_from(
            ProjectModel,
            {
                "id": str(uuid.uuid4()),
                "workspace_id": str(workspace.id),
                "slug": f"soak-project-{uuid.uuid4().hex[:8]}",
                "name": "REST Soak Project",
                "description": "REST hardening soak project",
                "created_at": "2026-04-21T00:00:00+00:00",
                "updated_at": "2026-04-21T00:00:00+00:00",
            },
        ),
    )
    for index in range(3):
        create_workspace(
            session,
            model_from(
                WorkspaceModel,
                {
                    "id": str(uuid.uuid4()),
                    "slug": f"soak-workspace-{index}-{uuid.uuid4().hex[:8]}",
                    "name": f"REST Soak Workspace {index}",
                    "description": "REST hardening soak workspace sample",
                    "created_at": "2026-04-21T00:00:00+00:00",
                    "updated_at": "2026-04-21T00:00:00+00:00",
                },
            ),
        )
    for index in range(3):
        create_task(
            session,
            model_from(
                TaskModel,
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": f"REST Soak Task {index}",
                    "state": "queued",
                    "description": "REST hardening soak task",
                    "contract": make_coding_task_contract(
                        surface_area=["src/api/soak"]
                    ),
                    "definition_of_done": ["done"],
                    "created_at": "2026-04-21T00:00:00+00:00",
                    "updated_at": "2026-04-21T00:00:00+00:00",
                },
            ),
        )

    tokens: list[str] = []
    actor_ids: list[uuid.UUID] = []
    for index in range(actor_count):
        actor_handle = f"rest-soak-{run_suffix}-{index:03d}"
        actor = create_actor(
            session,
            ActorModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "handle": actor_handle,
                    "actor_type": "agent",
                    "display_name": f"REST Soak Actor {index:03d}",
                    "auth_subject": actor_handle,
                    "is_active": True,
                    "created_at": "2026-04-21T00:00:00+00:00",
                    "updated_at": "2026-04-21T00:00:00+00:00",
                }
            ),
        )
        token_model, raw_token = issue_api_token(
            session,
            actor_id=actor.id,
            scopes=["admin"],
            expires_at=None,
        )
        del token_model
        actor_ids.append(actor.id)
        tokens.append(raw_token)

    return tokens, actor_ids, project.id


async def _soak_actor(
    *,
    actor_index: int,
    token: str,
    duration_seconds: int,
    rps_per_actor: float,
    metrics: dict[str, Any],
    client: httpx.AsyncClient,
) -> None:
    interval = 1.0 / rps_per_actor
    deadline = time.perf_counter() + duration_seconds
    iteration = 0

    while time.perf_counter() < deadline:
        endpoint = (
            "/v1/workspaces?limit=1" if iteration % 2 == 0 else "/v1/tasks?limit=1"
        )
        request_id = f"rest-hardening-soak-{actor_index}-{iteration}"
        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                client.get(
                    endpoint,
                    headers=_json_headers(
                        token=token,
                        request_id=request_id,
                        include_idempotency=False,
                    ),
                ),
                timeout=SOAK_REQUEST_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            metrics["request_exceptions"].append(
                {
                    "error_type": "TimeoutError",
                    "message": (
                        f"{endpoint} exceeded the {SOAK_REQUEST_TIMEOUT_SECONDS:.1f}s "
                        "request budget during the soak."
                    ),
                }
            )
        except httpx.HTTPError as error:
            metrics["request_exceptions"].append(
                {
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            )
        else:
            latency_ms = (time.perf_counter() - started) * 1000
            metrics["latencies_ms"].append(latency_ms)
            metrics["request_count"] += 1
            if response.status_code >= 500:
                metrics["server_errors"] += 1
            elif response.status_code == 429:
                metrics["rate_limited"] += 1
            elif response.status_code >= 400:
                metrics["other_errors"].append(
                    {
                        "status_code": response.status_code,
                        "body": response.text[:200],
                    }
                )
        await asyncio.sleep(max(0.0, interval - (time.perf_counter() - started)))
        iteration += 1


async def run_soak(
    *,
    duration_seconds: int,
    actor_count: int,
    rps_per_actor: float,
    max_read_p99_ms: float,
) -> tuple[dict[str, Any], int]:
    previous_auto_setup = os.environ.get("AGENTICQUEUE_AUTO_SETUP_ENABLED")
    os.environ["AGENTICQUEUE_AUTO_SETUP_ENABLED"] = "0"
    soak_config = _resolve_soak_config(
        duration_seconds=duration_seconds,
        actor_count=actor_count,
        rps_per_actor=rps_per_actor,
    )

    engine = sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        future=True,
        connect_args=get_psycopg_connect_args(),
    )
    pool_stats = _attach_pool_tracking(engine)
    failures = 0

    try:
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        with session_factory() as session:
            tokens, actor_ids, project_id = _seed_soak_data(
                session,
                actor_count=soak_config.effective_actor_count,
            )
            session.commit()

        app = create_app(session_factory=session_factory)
        transport = httpx.ASGITransport(app=app)
        metrics: dict[str, Any] = {
            "latencies_ms": [],
            "request_count": 0,
            "server_errors": 0,
            "rate_limited": 0,
            "other_errors": [],
            "request_exceptions": [],
            "timed_out_actors": 0,
        }

        started_at = dt.datetime.now(dt.UTC)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://agenticqueue.test",
            timeout=5.0,
        ) as client:
            tasks = [
                asyncio.create_task(
                    _soak_actor(
                        actor_index=index,
                        token=token,
                        duration_seconds=soak_config.effective_duration_seconds,
                        rps_per_actor=soak_config.effective_rps_per_actor,
                        metrics=metrics,
                        client=client,
                    )
                )
                for index, token in enumerate(tokens)
            ]
            _done, pending = await asyncio.wait(
                tasks,
                timeout=soak_config.effective_duration_seconds + 30,
            )
            if pending:
                metrics["timed_out_actors"] = len(pending)
                for task in pending:
                    task.cancel()
                await asyncio.wait(
                    pending,
                    timeout=SOAK_CANCEL_GRACE_SECONDS,
                )
        ended_at = dt.datetime.now(dt.UTC)

        latencies = cast(list[float], metrics["latencies_ms"])
        p50, p99, missing_latency_failure = _summarize_latency_metrics(latencies)
        failures_list: list[str] = []
        if missing_latency_failure is not None:
            failures_list.append(missing_latency_failure)
        if metrics["server_errors"] != 0:
            failures_list.append(
                f"Observed {metrics['server_errors']} server-error responses during the soak."
            )
        if metrics["rate_limited"] != 0:
            failures_list.append(
                f"Observed {metrics['rate_limited']} rate-limited responses below the 100 rps / 500 burst actor budget."
            )
        if p99 > max_read_p99_ms:
            failures_list.append(
                f"Read p99 {p99:.2f}ms exceeded the {max_read_p99_ms:.2f}ms budget."
            )
        if pool_stats.checked_out != 0:
            failures_list.append(
                f"Connection pool leak detected: {pool_stats.checked_out} checkouts remained open after the soak."
            )
        if metrics["other_errors"]:
            failures_list.append(
                f"Observed {len(metrics['other_errors'])} non-5xx client errors during the soak."
            )
        if metrics["request_exceptions"]:
            failures_list.append(
                f"Observed {len(metrics['request_exceptions'])} request exceptions during the soak."
            )
        if metrics["timed_out_actors"]:
            failures_list.append(
                f"Timed out waiting for {metrics['timed_out_actors']} soak actors to finish."
            )

        failures = len(failures_list)
        report = {
            "captured_at": ended_at.isoformat(),
            "status": "pass" if failures == 0 else "fail",
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_seconds": int((ended_at - started_at).total_seconds()),
            "actors": soak_config.effective_actor_count,
            "target_rps_per_actor": soak_config.effective_rps_per_actor,
            "latency_sample_count": len(latencies),
            "request_count": metrics["request_count"],
            "server_errors": metrics["server_errors"],
            "rate_limited": metrics["rate_limited"],
            "p50_ms": round(p50, 2),
            "p99_ms": round(p99, 2),
            "max_read_p99_ms": max_read_p99_ms,
            "pool": {
                "checked_out_after": pool_stats.checked_out,
                "peak_checked_out": pool_stats.peak_checked_out,
                "total_checkouts": pool_stats.total_checkouts,
                "total_checkins": pool_stats.total_checkins,
            },
            "sample_errors": metrics["other_errors"][:10],
            "sample_exceptions": metrics["request_exceptions"][:10],
            "timed_out_actors": metrics["timed_out_actors"],
            "failures": failures_list,
            "notes": {
                "project_id": str(project_id),
                "seeded_actor_count": len(actor_ids),
                "soak_profile": {
                    "ci_mode_enabled": soak_config.ci_mode_enabled,
                    "requested_duration_seconds": (
                        soak_config.requested_duration_seconds
                    ),
                    "effective_duration_seconds": (
                        soak_config.effective_duration_seconds
                    ),
                    "requested_actor_count": soak_config.requested_actor_count,
                    "effective_actor_count": soak_config.effective_actor_count,
                    "requested_rps_per_actor": (soak_config.requested_rps_per_actor),
                    "effective_rps_per_actor": (soak_config.effective_rps_per_actor),
                },
            },
        }
        return report, failures
    finally:
        if previous_auto_setup is None:
            os.environ.pop("AGENTICQUEUE_AUTO_SETUP_ENABLED", None)
        else:
            os.environ["AGENTICQUEUE_AUTO_SETUP_ENABLED"] = previous_auto_setup
        engine.dispose()


def _write_json(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()

    audit_report, audit_failures = run_audit()
    _write_json(args.output_json, audit_report)
    print(json.dumps(audit_report, indent=2))

    total_failures = audit_failures
    if args.soak_seconds > 0:
        soak_report, soak_failures = asyncio.run(
            run_soak(
                duration_seconds=args.soak_seconds,
                actor_count=args.actors,
                rps_per_actor=args.rps_per_actor,
                max_read_p99_ms=args.max_read_p99_ms,
            )
        )
        _write_json(args.soak_output_json, soak_report)
        print(json.dumps(soak_report, indent=2))
        total_failures += soak_failures

    return 0 if total_failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
