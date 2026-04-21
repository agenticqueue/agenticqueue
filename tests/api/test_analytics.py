from __future__ import annotations

import datetime as dt
import uuid

from fastapi.testclient import TestClient

from agenticqueue_api.models import (
    EdgeModel,
    EdgeRelation,
    PacketVersionModel,
    ProjectModel,
    RunModel,
    TaskModel,
    WorkspaceModel,
)
from agenticqueue_api.repo import (
    create_edge,
    create_packet_version,
    create_project,
    create_run,
    create_task,
    create_workspace,
)
from tests.entities import helpers as entity_helpers

engine = entity_helpers.engine
clean_database = entity_helpers.clean_database
session_factory = entity_helpers.session_factory
client = entity_helpers.client


def test_analytics_metrics_roll_up_cycle_time_blockers_retrieval_and_review_load(
    client: TestClient,
    session_factory,
) -> None:
    admin_actor = entity_helpers.seed_actor(
        session_factory,
        handle="analytics-admin",
        actor_type="admin",
        display_name="Analytics Admin",
    )
    reviewer_actor = entity_helpers.seed_actor(
        session_factory,
        handle="analytics-reviewer",
        actor_type="agent",
        display_name="Analytics Reviewer",
    )
    token = entity_helpers.seed_token(
        session_factory,
        actor_id=admin_actor.id,
        scopes=["admin"],
    )

    now = dt.datetime.now(dt.UTC).replace(microsecond=0)

    with session_factory() as session:
        workspace = create_workspace(
            session,
            entity_helpers.model_from(
                WorkspaceModel,
                {
                    "id": str(uuid.uuid4()),
                    "slug": "analytics-workspace",
                    "name": "Analytics Workspace",
                    "description": "Phase 7 analytics coverage",
                    "created_at": (now - dt.timedelta(hours=8)).isoformat(),
                    "updated_at": (now - dt.timedelta(hours=8)).isoformat(),
                },
            ),
        )
        project = create_project(
            session,
            entity_helpers.model_from(
                ProjectModel,
                {
                    "id": str(uuid.uuid4()),
                    "workspace_id": str(workspace.id),
                    "slug": "analytics-project",
                    "name": "Analytics Project",
                    "description": "Read-only dashboard validation",
                    "created_at": (now - dt.timedelta(hours=8)).isoformat(),
                    "updated_at": (now - dt.timedelta(hours=8)).isoformat(),
                },
            ),
        )

        blocker_task = create_task(
            session,
            entity_helpers.model_from(
                TaskModel,
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": "Ship the dependency edge renderer",
                    "state": "done",
                    "priority": 3,
                    "description": "Upstream analytics dependency",
                    "contract": entity_helpers.make_coding_task_contract(
                        surface_area=["analytics", "dependency-graph"]
                    ),
                    "definition_of_done": ["Done"],
                    "created_at": (now - dt.timedelta(hours=7)).isoformat(),
                    "updated_at": (now - dt.timedelta(hours=5)).isoformat(),
                },
            ),
        )
        blocked_task = create_task(
            session,
            entity_helpers.model_from(
                TaskModel,
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": "Render blocked-work heatmap",
                    "state": "blocked",
                    "priority": 4,
                    "description": "Waiting on the dependency renderer.",
                    "contract": entity_helpers.make_coding_task_contract(
                        surface_area=["analytics", "blocked-work"]
                    ),
                    "definition_of_done": ["Done"],
                    "created_at": (now - dt.timedelta(hours=4)).isoformat(),
                    "updated_at": (now - dt.timedelta(hours=2)).isoformat(),
                },
            ),
        )
        create_task(
            session,
            entity_helpers.model_from(
                TaskModel,
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": "Review the dashboard composition",
                    "state": "validated",
                    "priority": 2,
                    "description": "Waiting for review",
                    "contract": entity_helpers.make_coding_task_contract(
                        surface_area=["analytics", "review"]
                    ),
                    "definition_of_done": ["Done"],
                    "created_at": (now - dt.timedelta(hours=2)).isoformat(),
                    "updated_at": (now - dt.timedelta(minutes=45)).isoformat(),
                },
            ),
        )
        create_task(
            session,
            entity_helpers.model_from(
                TaskModel,
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": "Investigate the failed smoke run",
                    "state": "queued",
                    "priority": 1,
                    "labels": ["needs:human-review"],
                    "description": "Queued for a human check",
                    "contract": entity_helpers.make_coding_task_contract(
                        surface_area=["analytics", "smoke"]
                    ),
                    "definition_of_done": ["Done"],
                    "created_at": (now - dt.timedelta(hours=1)).isoformat(),
                    "updated_at": (now - dt.timedelta(minutes=30)).isoformat(),
                },
            ),
        )
        learning_source_task = create_task(
            session,
            entity_helpers.model_from(
                TaskModel,
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": "Capture retrieval heuristics",
                    "state": "done",
                    "priority": 2,
                    "description": "Analytics retrieval source",
                    "contract": entity_helpers.make_coding_task_contract(
                        surface_area=["analytics", "retrieval"]
                    ),
                    "definition_of_done": ["Done"],
                    "created_at": (now - dt.timedelta(hours=6)).isoformat(),
                    "updated_at": (now - dt.timedelta(hours=4)).isoformat(),
                },
            ),
        )
        unrelated_learning_task = create_task(
            session,
            entity_helpers.model_from(
                TaskModel,
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": "Tune deployment smoke retries",
                    "state": "done",
                    "priority": 1,
                    "description": "Unrelated retrieval source",
                    "contract": entity_helpers.make_coding_task_contract(
                        surface_area=["deploy", "retries"]
                    ),
                    "definition_of_done": ["Done"],
                    "created_at": (now - dt.timedelta(hours=5)).isoformat(),
                    "updated_at": (now - dt.timedelta(hours=3)).isoformat(),
                },
            ),
        )
        packet_task = create_task(
            session,
            entity_helpers.model_from(
                TaskModel,
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": "Assemble analytics packet",
                    "state": "queued",
                    "priority": 3,
                    "description": "Packet target task",
                    "contract": entity_helpers.make_coding_task_contract(
                        surface_area=["analytics", "retrieval"]
                    ),
                    "definition_of_done": ["Done"],
                    "created_at": (now - dt.timedelta(hours=2)).isoformat(),
                    "updated_at": (now - dt.timedelta(minutes=20)).isoformat(),
                },
            ),
        )

        create_edge(
            session,
            EdgeModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "created_at": (now - dt.timedelta(hours=2)).isoformat(),
                    "src_entity_type": "task",
                    "src_id": str(blocked_task.id),
                    "dst_entity_type": "task",
                    "dst_id": str(blocker_task.id),
                    "relation": EdgeRelation.GATED_BY.value,
                    "metadata": {},
                    "created_by": str(admin_actor.id),
                }
            ),
        )

        create_run(
            session,
            RunModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "task_id": str(blocker_task.id),
                    "actor_id": str(admin_actor.id),
                    "status": "done",
                    "started_at": (now - dt.timedelta(hours=5)).isoformat(),
                    "ended_at": (now - dt.timedelta(hours=4, minutes=30)).isoformat(),
                    "summary": "Dependency edge renderer shipped.",
                    "details": {"attempt": 1},
                    "created_at": (now - dt.timedelta(hours=5)).isoformat(),
                    "updated_at": (now - dt.timedelta(hours=4, minutes=30)).isoformat(),
                }
            ),
        )
        create_run(
            session,
            RunModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "task_id": str(blocked_task.id),
                    "actor_id": str(admin_actor.id),
                    "status": "rejected",
                    "started_at": (now - dt.timedelta(hours=3)).isoformat(),
                    "ended_at": (now - dt.timedelta(hours=2, minutes=15)).isoformat(),
                    "summary": "Heatmap draft failed review.",
                    "details": {"attempt": 2},
                    "created_at": (now - dt.timedelta(hours=3)).isoformat(),
                    "updated_at": (now - dt.timedelta(hours=2, minutes=15)).isoformat(),
                }
            ),
        )
        create_run(
            session,
            RunModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "task_id": str(learning_source_task.id),
                    "actor_id": str(reviewer_actor.id),
                    "status": "completed",
                    "started_at": (now - dt.timedelta(hours=2)).isoformat(),
                    "ended_at": (now - dt.timedelta(hours=1, minutes=20)).isoformat(),
                    "summary": "Retrieval heuristics validated.",
                    "details": {"attempt": 1},
                    "created_at": (now - dt.timedelta(hours=2)).isoformat(),
                    "updated_at": (now - dt.timedelta(hours=1, minutes=20)).isoformat(),
                }
            ),
        )

        create_packet_version(
            session,
            PacketVersionModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "task_id": str(packet_task.id),
                    "packet_hash": "sha256:analytics-packet-sample",
                    "payload": {
                        "repo_scope": {
                            "surface_area": ["analytics", "retrieval"],
                        },
                        "retrieval_tiers_used": ["surface_area", "graph", "metadata"],
                        "relevant_learnings": [
                            {
                                "task_id": str(learning_source_task.id),
                                "title": "Analytics retrieval heuristic",
                            },
                            {
                                "task_id": str(unrelated_learning_task.id),
                                "title": "Deployment retry heuristic",
                            },
                        ],
                    },
                    "created_at": (now - dt.timedelta(minutes=15)).isoformat(),
                }
            ),
        )

        session.commit()

    response = client.get(
        "/v1/analytics/metrics?window=90d",
        headers=entity_helpers.auth_headers(token),
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["window"]["key"] == "90d"
    assert payload["window"]["days"] == 90

    cycle_time = {metric["task_type"]: metric for metric in payload["cycle_time"]}
    assert cycle_time["coding-task"]["count"] == 3
    assert cycle_time["coding-task"]["median_hours"] > 0
    assert (
        cycle_time["coding-task"]["p95_hours"]
        >= cycle_time["coding-task"]["median_hours"]
    )

    blocked_heatmap = payload["blocked_heatmap"]
    assert len(blocked_heatmap) == 1
    assert blocked_heatmap[0]["blocker_title"] == blocker_task.title
    assert blocked_heatmap[0]["task_count"] == 1
    assert blocked_heatmap[0]["sample_refs"] == [f"job-{blocked_task.sequence}"]

    histogram_total = sum(
        bucket["count"] for bucket in payload["handoff_latency_histogram"]
    )
    assert histogram_total == 3

    success_rates = {
        metric["actor"]: metric for metric in payload["agent_success_rates"]
    }
    assert success_rates["analytics-admin"]["complete_count"] == 1
    assert success_rates["analytics-admin"]["error_count"] == 1
    assert success_rates["analytics-admin"]["success_rate"] == 0.5
    assert success_rates["analytics-reviewer"]["complete_count"] == 1
    assert success_rates["analytics-reviewer"]["success_rate"] == 1.0

    retrieval_precision = payload["retrieval_precision"]
    assert retrieval_precision["sample_size"] == 1
    assert retrieval_precision["precision_at_5"] == 0.5
    assert retrieval_precision["precision_at_10"] == 0.5

    review_load = payload["review_load"]
    assert len(review_load) == 90
    assert max(point["count"] for point in review_load) >= 2
