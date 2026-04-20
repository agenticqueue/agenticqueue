from __future__ import annotations

import uuid

from agenticqueue_api.models import EdgeModel, LearningModel, PolicyModel

from .helpers import (
    assert_error_shape,
    auth_headers,
    model_from,
    seed_actor,
    seed_token,
    seed_workspace,
)


def test_openapi_route_is_available_with_bearer_auth(
    client,
    session_factory,
) -> None:
    actor = seed_actor(
        session_factory,
        handle="openapi-admin",
        actor_type="admin",
        display_name="OpenAPI Admin",
    )
    token = seed_token(session_factory, actor_id=actor.id, scopes=["admin"])

    response = client.get("/openapi.json", headers=auth_headers(token))

    assert response.status_code == 200
    assert "/v1/workspaces" in response.json()["paths"]
    assert "/v1/edges/{entity_id}" in response.json()["paths"]


def test_policy_learning_and_edge_filters_cover_int_date_and_enum_paths(
    client,
    session_factory,
    deps,
) -> None:
    actor = seed_actor(
        session_factory,
        handle="meta-admin",
        actor_type="admin",
        display_name="Meta Admin",
    )
    token = seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=[
            "policy:read",
            "policy:write",
            "learning:read",
            "learning:write",
            "edge:read",
            "edge:write",
        ],
    )

    policy_payload = model_from(
        PolicyModel,
        {
            "id": str(uuid.uuid4()),
            "workspace_id": str(deps.workspace_id),
            "name": "default-coding",
            "version": "1.0.0",
            "hitl_required": False,
            "autonomy_tier": 3,
            "body": {"rule": "allow"},
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        },
    ).model_dump(mode="json")
    policy_response = client.post(
        "/v1/policies", headers=auth_headers(token), json=policy_payload
    )
    assert policy_response.status_code == 201
    assert (
        client.get(
            "/v1/policies",
            headers=auth_headers(token),
            params={"autonomy_tier": "3"},
        ).status_code
        == 200
    )

    learning_payload = model_from(
        LearningModel,
        {
            "id": str(uuid.uuid4()),
            "task_id": str(deps.task_id),
            "owner_actor_id": str(deps.actor_id),
            "title": "Learning Alpha",
            "learning_type": "pattern",
            "what_happened": "A thing happened",
            "what_learned": "A thing was learned",
            "action_rule": "Do the better thing",
            "applies_when": "Always",
            "does_not_apply_when": "Never",
            "evidence": ["run:1"],
            "scope": "project",
            "confidence": "confirmed",
            "status": "active",
            "review_date": "2026-04-21",
            "embedding": None,
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        },
    ).model_dump(mode="json")
    learning_response = client.post(
        "/v1/learnings", headers=auth_headers(token), json=learning_payload
    )
    assert learning_response.status_code == 201
    assert (
        client.get(
            "/v1/learnings",
            headers=auth_headers(token),
            params={"review_date": "2026-04-21"},
        ).status_code
        == 200
    )

    edge_payload = model_from(
        EdgeModel,
        {
            "id": str(uuid.uuid4()),
            "src_entity_type": "task",
            "src_id": str(deps.task_id),
            "dst_entity_type": "project",
            "dst_id": str(deps.project_id),
            "relation": "depends_on",
            "metadata": {},
            "created_by": str(deps.actor_id),
            "created_at": "2026-04-20T00:00:00+00:00",
        },
    ).model_dump(mode="json")
    edge_response = client.post(
        "/v1/edges", headers=auth_headers(token), json=edge_payload
    )
    assert edge_response.status_code == 201
    assert (
        client.get(
            "/v1/edges",
            headers=auth_headers(token),
            params={"relation": "depends_on"},
        ).status_code
        == 200
    )


def test_policy_attach_detach_and_post_attach_immutability(
    client,
    session_factory,
    deps,
) -> None:
    actor = seed_actor(
        session_factory,
        handle="policy-admin",
        actor_type="admin",
        display_name="Policy Admin",
    )
    token = seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=[
            "policy:read",
            "policy:write",
            "workspace:read",
            "workspace:write",
            "project:read",
            "project:write",
            "task:read",
            "task:write",
        ],
    )
    policy_payload = model_from(
        PolicyModel,
        {
            "id": str(uuid.uuid4()),
            "workspace_id": None,
            "name": "default-coding",
            "version": "1.0.0",
            "hitl_required": True,
            "autonomy_tier": 3,
            "capabilities": ["read_repo", "run_tests"],
            "body": {},
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        },
    ).model_dump(mode="json")
    create_response = client.post(
        "/v1/policies",
        headers=auth_headers(token),
        json=policy_payload,
    )
    assert create_response.status_code == 201
    policy_id = create_response.json()["id"]

    workspace_attach = client.patch(
        f"/v1/workspaces/{deps.workspace_id}",
        headers=auth_headers(token),
        json={"policy_id": policy_id},
    )
    assert workspace_attach.status_code == 200
    assert workspace_attach.json()["policy_id"] == policy_id

    project_attach = client.patch(
        f"/v1/projects/{deps.project_id}",
        headers=auth_headers(token),
        json={"policy_id": policy_id},
    )
    assert project_attach.status_code == 200
    assert project_attach.json()["policy_id"] == policy_id

    task_attach = client.patch(
        f"/v1/tasks/{deps.task_id}",
        headers=auth_headers(token),
        json={"policy_id": policy_id},
    )
    assert task_attach.status_code == 200
    assert task_attach.json()["policy_id"] == policy_id

    immutable_update = client.patch(
        f"/v1/policies/{policy_id}",
        headers=auth_headers(token),
        json={"hitl_required": False},
    )
    assert_error_shape(immutable_update, status_code=409, error_code="conflict")

    task_detach = client.patch(
        f"/v1/tasks/{deps.task_id}",
        headers=auth_headers(token),
        json={"policy_id": None},
    )
    assert task_detach.status_code == 200
    assert task_detach.json()["policy_id"] is None


def test_duplicate_create_invalid_filter_invalid_value_invalid_payload_and_immutable_patch_are_structured(
    client,
    session_factory,
    deps,
    core_specs_by_resource,
) -> None:
    actor = seed_actor(
        session_factory,
        handle="workspace-admin",
        actor_type="admin",
        display_name="Workspace Admin",
    )
    token = seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=["workspace:read", "workspace:write", "actor:read", "edge:read"],
    )
    workspace_spec = core_specs_by_resource["workspaces"]

    workspace_payload = workspace_spec.create_payload(deps)
    create_response = client.post(
        "/v1/workspaces",
        headers=auth_headers(token),
        json=workspace_payload,
    )
    assert create_response.status_code == 201
    created_id = create_response.json()["id"]

    duplicate_response = client.post(
        "/v1/workspaces",
        headers=auth_headers(token),
        json={**workspace_spec.create_payload(deps), "slug": workspace_payload["slug"]},
    )
    assert_error_shape(duplicate_response, status_code=409, error_code="conflict")

    conflicting_slug = "workspace-conflict"
    seed_workspace(session_factory, slug=conflicting_slug, name="Workspace Conflict")
    conflict_update = client.patch(
        f"/v1/workspaces/{created_id}",
        headers=auth_headers(token),
        json={"slug": conflicting_slug},
    )
    assert_error_shape(conflict_update, status_code=409, error_code="conflict")

    unknown_filter_response = client.get(
        "/v1/workspaces",
        headers=auth_headers(token),
        params={"unknown": "value"},
    )
    assert_error_shape(
        unknown_filter_response, status_code=400, error_code="bad_request"
    )

    invalid_bool_filter = client.get(
        "/v1/actors",
        headers=auth_headers(token),
        params={"is_active": "maybe"},
    )
    assert_error_shape(invalid_bool_filter, status_code=400, error_code="bad_request")

    invalid_enum_filter = client.get(
        "/v1/edges",
        headers=auth_headers(token),
        params={"relation": "not-a-relation"},
    )
    assert_error_shape(invalid_enum_filter, status_code=400, error_code="bad_request")

    invalid_payload = client.post(
        "/v1/workspaces",
        headers=auth_headers(token),
        json={"slug": "missing-fields"},
    )
    assert_error_shape(invalid_payload, status_code=422, error_code="validation_error")

    immutable_patch = client.patch(
        f"/v1/workspaces/{created_id}",
        headers=auth_headers(token),
        json={"id": str(uuid.uuid4())},
    )
    assert_error_shape(immutable_patch, status_code=400, error_code="bad_request")
