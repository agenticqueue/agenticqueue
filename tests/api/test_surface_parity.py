from __future__ import annotations

from pathlib import Path
import shutil
import uuid

from fastapi.testclient import TestClient
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.models import (
    AuditLogRecord,
    CapabilityKey,
    DecisionModel,
    EdgeModel,
    EdgeRelation,
    TaskModel,
    TaskRecord,
)
from agenticqueue_api.repo import create_decision, create_edge, create_task
from agenticqueue_api.task_type_registry import TaskTypeRegistry
from tests.aq.test_packet_mcp import _seed_task_with_token
from tests.entities import helpers as entity_helpers

engine = entity_helpers.engine
clean_database = entity_helpers.clean_database
session_factory = entity_helpers.session_factory
client = entity_helpers.client


def _authorization_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_actor_token(
    session_factory: sessionmaker[Session],
    *,
    handle: str,
    actor_type: str,
    scopes: list[str],
) -> tuple[str, str]:
    actor = entity_helpers.seed_actor(
        session_factory,
        handle=handle,
        actor_type=actor_type,
        display_name=handle.replace("-", " ").title(),
    )
    token = entity_helpers.seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=scopes,
    )
    return str(actor.id), token


def _task_types_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "task_types"


def _build_temp_task_type_registry(tmp_path: Path) -> TaskTypeRegistry:
    copied_dir = tmp_path / "task_types"
    copied_dir.mkdir()
    for source in _task_types_dir().iterdir():
        shutil.copy2(source, copied_dir / source.name)
    registry = TaskTypeRegistry(copied_dir, reload_enabled=False)
    registry.load()
    return registry


def test_surface_paths_are_served_and_documented(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _, admin_token = _seed_actor_token(
        session_factory,
        handle="surface-docs-admin",
        actor_type="admin",
        scopes=["admin"],
    )

    health_response = client.get("/healthz")
    assert health_response.status_code == 200
    assert health_response.json() == {
        "status": "ok",
        "version": "0.1.0",
    }

    stats_response = client.get(
        "/stats",
        headers=_authorization_headers(admin_token),
    )
    assert stats_response.status_code == 200
    stats_payload = stats_response.json()
    assert set(stats_payload) == {"idempotency", "packet_cache", "mcp"}
    assert stats_payload["packet_cache"]["enabled"] is True

    openapi_response = client.get(
        "/openapi.json",
        headers=_authorization_headers(admin_token),
    )
    assert openapi_response.status_code == 200
    paths = set(openapi_response.json()["paths"])
    assert {
        "/healthz",
        "/stats",
        "/setup",
        "/v1/actors/me/rotate-key",
        "/v1/task-types/{task_type_name}",
        "/v1/tasks/claim",
        "/v1/tasks/{task_id}/release",
        "/v1/tasks/{task_id}/reset",
        "/v1/tasks/{task_id}/comments",
        "/v1/decisions/{decision_id}/supersede",
        "/v1/decisions/{decision_id}/link",
        "/v1/graph/neighborhood/{entity_id}",
        "/v1/graph/traverse/{entity_id}",
        "/v1/graph/surface",
    }.issubset(paths)


def test_rotate_own_key_and_task_type_detail_update_routes_work(
    client: TestClient,
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    _, actor_token = _seed_actor_token(
        session_factory,
        handle="surface-rotate-agent",
        actor_type="agent",
        scopes=["self"],
    )
    _, admin_token = _seed_actor_token(
        session_factory,
        handle="surface-types-admin",
        actor_type="admin",
        scopes=["admin"],
    )

    rotate_response = client.post(
        "/v1/actors/me/rotate-key",
        headers=entity_helpers.auth_headers(actor_token),
        json={},
    )
    assert rotate_response.status_code == 200
    rotated_payload = rotate_response.json()
    assert rotated_payload["token"].startswith("aq__")
    assert rotated_payload["api_token"]["token_prefix"].startswith("aq__")

    tokens_response = client.get(
        "/v1/auth/tokens",
        headers=_authorization_headers(rotated_payload["token"]),
    )
    assert tokens_response.status_code == 200
    assert tokens_response.json()["actor"]["handle"] == "surface-rotate-agent"

    get_task_type_response = client.get(
        "/v1/task-types/coding-task",
        headers=_authorization_headers(rotated_payload["token"]),
    )
    assert get_task_type_response.status_code == 200
    assert get_task_type_response.json()["name"] == "coding-task"

    task_type_registry = _build_temp_task_type_registry(tmp_path)
    with TestClient(
        create_app(
            session_factory=session_factory,
            task_type_registry=task_type_registry,
        )
    ) as temp_client:
        update_response = temp_client.patch(
            "/v1/task-types/coding-task",
            headers=entity_helpers.auth_headers(admin_token),
            json={
                "schema": {
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string"},
                        "surface_area": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["repo"],
                    "additionalProperties": True,
                },
                "policy": {
                    "autonomy_tier": 2,
                    "allow_human_override": True,
                },
            },
        )

        assert update_response.status_code == 200
        assert update_response.json()["schema"]["required"] == ["repo"]
        assert update_response.json()["policy"]["autonomy_tier"] == 2

        detail_response = temp_client.get(
            "/v1/task-types/coding-task",
            headers=_authorization_headers(admin_token),
        )
        assert detail_response.status_code == 200
        assert detail_response.json()["policy"]["allow_human_override"] is True


def test_task_action_parity_routes_claim_release_comment_and_reset(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    actor_id, project_id, task_id, token = _seed_task_with_token(
        session_factory,
        handle="surface-actions-agent",
        task_state="queued",
    )
    _, admin_token = _seed_actor_token(
        session_factory,
        handle="surface-actions-admin",
        actor_type="admin",
        scopes=["admin"],
    )

    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        assert task is not None
        task.labels = ["needs:coding"]
        session.commit()

    claim_response = client.post(
        f"/v1/tasks/claim?project_id={project_id}&labels=needs:coding",
        headers=entity_helpers.auth_headers(token),
    )
    assert claim_response.status_code == 200
    assert claim_response.json()["id"] == str(task_id)
    assert claim_response.json()["state"] == "claimed"

    release_response = client.post(
        f"/v1/tasks/{task_id}/release",
        headers=entity_helpers.auth_headers(token),
    )
    assert release_response.status_code == 200
    assert release_response.json()["state"] == "todo"
    assert release_response.json()["claimed_by_actor_id"] is None

    comment_response = client.post(
        f"/v1/tasks/{task_id}/comments",
        headers=entity_helpers.auth_headers(token),
        json={"body": "Need another pass before review."},
    )
    assert comment_response.status_code == 200
    assert comment_response.json() == {
        "job_id": str(task_id),
        "commented": True,
    }

    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        assert task is not None
        task.state = "in_progress"
        task.attempt_count = 2
        task.last_failure = {"message": "boom"}
        task.claimed_by_actor_id = actor_id
        session.commit()

    reset_response = client.post(
        f"/v1/tasks/{task_id}/reset",
        headers=entity_helpers.auth_headers(admin_token),
    )
    assert reset_response.status_code == 200
    assert reset_response.json()["state"] == "queued"
    assert reset_response.json()["attempt_count"] == 0
    assert reset_response.json()["last_failure"] is None

    with session_factory() as session:
        audit_rows = session.scalars(
            sa.select(AuditLogRecord)
            .where(
                AuditLogRecord.entity_type == "task",
                AuditLogRecord.entity_id == task_id,
                AuditLogRecord.action.in_(("JOB_COMMENTED", "JOB_RESET")),
            )
            .order_by(AuditLogRecord.created_at.asc(), AuditLogRecord.id.asc())
        ).all()

    assert [row.action for row in audit_rows] == ["JOB_COMMENTED", "JOB_RESET"]


def test_graph_and_decision_helper_routes_work(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    actor_id, project_id, task_id, token = _seed_task_with_token(
        session_factory,
        handle="surface-graph-agent",
        grant_capabilities=(
            CapabilityKey.QUERY_GRAPH,
            CapabilityKey.UPDATE_TASK,
        ),
        token_scopes=("decision:write",),
    )

    with session_factory() as session:
        source_task = session.get(TaskRecord, task_id)
        assert source_task is not None
        source_task.contract = entity_helpers.make_coding_task_contract(
            surface_area=["src/api/parity"]
        )

        sibling_task = create_task(
            session,
            TaskModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project_id),
                    "task_type": "coding-task",
                    "title": "Surface Parity Sibling",
                    "state": "queued",
                    "description": "Sibling task for graph traversal tests.",
                    "contract": entity_helpers.make_coding_task_contract(
                        surface_area=["src/web/parity"]
                    ),
                    "definition_of_done": ["done"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        create_edge(
            session,
            EdgeModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "src_entity_type": "task",
                    "src_id": str(task_id),
                    "dst_entity_type": "task",
                    "dst_id": str(sibling_task.id),
                    "relation": EdgeRelation.DEPENDS_ON.value,
                    "metadata": {},
                    "created_by": actor_id,
                }
            ),
        )
        prior_decision = create_decision(
            session,
            DecisionModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "task_id": str(task_id),
                    "run_id": None,
                    "actor_id": actor_id,
                    "summary": "Initial parity decision",
                    "rationale": "Document the first pass.",
                    "decided_at": "2026-04-20T00:00:00+00:00",
                    "embedding": None,
                    "created_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        replacement_decision = create_decision(
            session,
            DecisionModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "task_id": str(sibling_task.id),
                    "run_id": None,
                    "actor_id": actor_id,
                    "summary": "Replacement parity decision",
                    "rationale": "Supersede the initial path.",
                    "decided_at": "2026-04-20T00:05:00+00:00",
                    "embedding": None,
                    "created_at": "2026-04-20T00:05:00+00:00",
                }
            ),
        )
        session.commit()

    neighborhood_response = client.get(
        f"/v1/graph/neighborhood/{task_id}",
        headers=_authorization_headers(token),
        params={"entity_type": "task", "hops": 1},
    )
    assert neighborhood_response.status_code == 200
    assert {item["entity_id"] for item in neighborhood_response.json()["items"]} == {
        str(sibling_task.id)
    }

    traverse_response = client.get(
        f"/v1/graph/traverse/{task_id}",
        headers=_authorization_headers(token),
        params={"entity_type": "task", "direction": "descendants"},
    )
    assert traverse_response.status_code == 200
    assert traverse_response.json()["direction"] == "descendants"
    assert {item["entity_id"] for item in traverse_response.json()["items"]} == {
        str(sibling_task.id)
    }

    surface_response = client.get(
        "/v1/graph/surface",
        headers=_authorization_headers(token),
        params={"tag": "src/api/parity"},
    )
    assert surface_response.status_code == 200
    assert surface_response.json()["items"] == [
        {
            "entity_type": "task",
            "entity_id": str(task_id),
            "matched_tags": ["src/api/parity"],
        }
    ]

    link_response = client.post(
        f"/v1/decisions/{prior_decision.id}/link",
        headers=entity_helpers.auth_headers(token),
        json={"job_id": str(sibling_task.id)},
    )
    assert link_response.status_code == 201
    assert link_response.json()["relation"] == EdgeRelation.INFORMED_BY.value
    assert link_response.json()["dst_id"] == str(sibling_task.id)

    supersede_response = client.post(
        f"/v1/decisions/{prior_decision.id}/supersede",
        headers=entity_helpers.auth_headers(token),
        json={"replaced_by": str(replacement_decision.id)},
    )
    assert supersede_response.status_code == 201
    assert supersede_response.json()["relation"] == EdgeRelation.SUPERSEDES.value
    assert supersede_response.json()["src_id"] == str(replacement_decision.id)
    assert supersede_response.json()["dst_id"] == str(prior_decision.id)
