"""AgenticQueue MCP submit/admin/project tool registrations."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
import uuid
from typing import Any

from fastmcp import FastMCP
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.auth import issue_api_token, revoke_api_token
from agenticqueue_api.capabilities import ensure_actor_has_capability
from agenticqueue_api.models import CapabilityKey
from agenticqueue_api.models.edge import EdgeRelation
from agenticqueue_api.mcp.common import (
    call_internal_api,
    run_session_tool,
    serialize_model,
    surface_error,
)
from agenticqueue_api.policy import load_policy_pack as read_policy_pack
from agenticqueue_api.repo import ancestors, claim_next, descendants, neighbors
from agenticqueue_api.task_type_registry import TaskTypeRegistry


def _register_create_tool(
    mcp: FastMCP,
    *,
    name: str,
    path: str,
    app: Any,
) -> str:
    @mcp.tool(name=name, annotations={"readOnlyHint": False, "openWorldHint": False})
    def _tool(
        payload: dict[str, Any],
        token: str | None = None,
    ) -> dict[str, Any]:
        return call_internal_api(
            app,
            method="POST",
            path=path,
            token=token,
            json_body=payload,
        )

    return name


def _register_list_tool(
    mcp: FastMCP,
    *,
    name: str,
    path: str,
    app: Any,
) -> str:
    @mcp.tool(name=name, annotations={"readOnlyHint": True, "openWorldHint": False})
    def _tool(
        token: str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        params = dict(filters or {})
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        return call_internal_api(
            app,
            method="GET",
            path=path,
            token=token,
            params=params or None,
        )

    return name


def _register_get_tool(
    mcp: FastMCP,
    *,
    name: str,
    path_template: str,
    app: Any,
) -> str:
    @mcp.tool(name=name, annotations={"readOnlyHint": True, "openWorldHint": False})
    def _tool(
        entity_id: uuid.UUID,
        token: str | None = None,
    ) -> dict[str, Any]:
        return call_internal_api(
            app,
            method="GET",
            path=path_template.format(entity_id=entity_id),
            token=token,
        )

    return name


def _register_update_tool(
    mcp: FastMCP,
    *,
    name: str,
    path_template: str,
    app: Any,
) -> str:
    @mcp.tool(name=name, annotations={"readOnlyHint": False, "openWorldHint": False})
    def _tool(
        entity_id: uuid.UUID,
        payload: dict[str, Any],
        token: str | None = None,
    ) -> dict[str, Any]:
        return call_internal_api(
            app,
            method="PATCH",
            path=path_template.format(entity_id=entity_id),
            token=token,
            json_body=payload,
        )

    return name


def _register_delete_tool(
    mcp: FastMCP,
    *,
    name: str,
    path_template: str,
    app: Any,
) -> str:
    @mcp.tool(name=name, annotations={"readOnlyHint": False, "openWorldHint": False})
    def _tool(
        entity_id: uuid.UUID,
        token: str | None = None,
    ) -> dict[str, Any]:
        return call_internal_api(
            app,
            method="DELETE",
            path=path_template.format(entity_id=entity_id),
            token=token,
        )

    return name


def register_submit_tools(
    mcp: FastMCP,
    *,
    app: Any,
    session_factory: sessionmaker[Session],
    task_type_registry: TaskTypeRegistry,
) -> set[str]:
    """Register canonical submit/admin/project tools on the shared server."""

    registered: set[str] = set()

    for name, path in (
        ("create_actor", "/v1/actors"),
        ("create_project", "/v1/workspaces"),
        ("create_pipeline", "/v1/projects"),
        ("create_job", "/v1/tasks"),
        ("create_decision", "/v1/decisions"),
        ("attach_artifact", "/v1/artifacts"),
        ("grant_capability", "/v1/capabilities/grant"),
    ):
        registered.add(_register_create_tool(mcp, name=name, path=path, app=app))

    for name, path in (
        ("list_actors", "/v1/actors"),
        ("list_projects", "/v1/workspaces"),
        ("list_pipelines", "/v1/projects"),
        ("list_jobs", "/v1/tasks"),
        ("list_decisions", "/v1/decisions"),
        ("list_learnings", "/v1/learnings"),
        ("list_policy_packs", "/v1/policies"),
        ("list_runs", "/v1/runs"),
        ("list_artifacts", "/v1/artifacts"),
        ("list_task_types", "/v1/task-types"),
    ):
        registered.add(_register_list_tool(mcp, name=name, path=path, app=app))

    for name, path_template in (
        ("get_project", "/v1/workspaces/{entity_id}"),
        ("get_pipeline", "/v1/projects/{entity_id}"),
        ("get_job", "/v1/tasks/{entity_id}"),
        ("get_decision", "/v1/decisions/{entity_id}"),
        ("get_learning", "/v1/learnings/{entity_id}"),
        ("get_policy_pack", "/v1/policies/{entity_id}"),
        ("get_run", "/v1/runs/{entity_id}"),
        ("get_artifact", "/v1/artifacts/{entity_id}"),
    ):
        registered.add(
            _register_get_tool(
                mcp,
                name=name,
                path_template=path_template,
                app=app,
            )
        )

    for name, path_template in (
        ("update_project", "/v1/workspaces/{entity_id}"),
        ("update_pipeline", "/v1/projects/{entity_id}"),
        ("update_job", "/v1/tasks/{entity_id}"),
        ("edit_learning", "/v1/learnings/{entity_id}"),
    ):
        registered.add(
            _register_update_tool(
                mcp,
                name=name,
                path_template=path_template,
                app=app,
            )
        )

    for name, path_template in (
        ("revoke_actor", "/v1/actors/{entity_id}"),
        ("archive_project", "/v1/workspaces/{entity_id}"),
        ("cancel_pipeline", "/v1/projects/{entity_id}"),
    ):
        registered.add(
            _register_delete_tool(
                mcp,
                name=name,
                path_template=path_template,
                app=app,
            )
        )

    @mcp.tool(
        name="get_self", annotations={"readOnlyHint": True, "openWorldHint": False}
    )
    def get_self(token: str | None = None) -> dict[str, Any]:
        def _callback(session: Session, authenticated) -> dict[str, Any]:
            return {
                "actor": authenticated.actor.model_dump(mode="json"),
                "api_token": authenticated.api_token.model_dump(mode="json"),
            }

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="get-self",
            callback=_callback,
        )

    registered.add("get_self")

    @mcp.tool(
        name="rotate_own_key",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def rotate_own_key(
        token: str | None = None,
        scopes: list[str] | None = None,
        expires_at: dt.datetime | None = None,
    ) -> dict[str, Any]:
        def _callback(session: Session, authenticated) -> dict[str, Any]:
            revoke_api_token(session, authenticated.api_token.id)
            api_token, raw_token = issue_api_token(
                session,
                actor_id=authenticated.actor.id,
                scopes=scopes or authenticated.api_token.scopes,
                expires_at=expires_at,
            )
            return {
                "token": raw_token,
                "api_token": api_token.model_dump(mode="json"),
            }

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="rotate-own-key",
            callback=_callback,
            mutation=True,
        )

    registered.add("rotate_own_key")

    @mcp.tool(
        name="register_task_type",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def register_task_type(
        name: str,
        schema: dict[str, Any],
        policy: dict[str, Any],
        token: str | None = None,
    ) -> dict[str, Any]:
        return call_internal_api(
            app,
            method="POST",
            path="/v1/task-types",
            token=token,
            json_body={"name": name, "schema": schema, "policy": policy},
        )

    registered.add("register_task_type")

    @mcp.tool(
        name="get_task_type",
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    def get_task_type(
        name: str,
        token: str | None = None,
    ) -> dict[str, Any]:
        def _callback(session: Session, authenticated) -> dict[str, Any]:
            del session, authenticated
            try:
                definition = task_type_registry.get(name)
            except ValueError as error:
                raise surface_error(404, str(error)) from error
            return {
                "name": definition.name,
                "schema": definition.schema,
                "policy": definition.policy,
                "schema_path": str(definition.schema_path),
                "policy_path": str(definition.policy_path),
            }

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="get-task-type",
            callback=_callback,
        )

    registered.add("get_task_type")

    @mcp.tool(
        name="update_task_type",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def update_task_type(
        name: str,
        schema: dict[str, Any],
        policy: dict[str, Any],
        token: str | None = None,
    ) -> dict[str, Any]:
        def _callback(session: Session, authenticated) -> dict[str, Any]:
            del session
            if authenticated.actor.actor_type != "admin":
                raise surface_error(403, "Admin actor required")
            definition = task_type_registry.register(
                name=name,
                schema=schema,
                policy=policy,
            )
            return {
                "name": definition.name,
                "schema": definition.schema,
                "policy": definition.policy,
                "schema_path": str(definition.schema_path),
                "policy_path": str(definition.policy_path),
            }

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="update-task-type",
            callback=_callback,
            mutation=True,
        )

    registered.add("update_task_type")

    @mcp.tool(
        name="revoke_capability",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def revoke_capability(
        grant_id: uuid.UUID,
        token: str | None = None,
    ) -> dict[str, Any]:
        return call_internal_api(
            app,
            method="POST",
            path="/v1/capabilities/revoke",
            token=token,
            json_body={"grant_id": str(grant_id)},
        )

    registered.add("revoke_capability")

    @mcp.tool(
        name="claim_next_job",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def claim_next_job(
        token: str | None = None,
        labels: list[str] | None = None,
        claim_states: list[str] | None = None,
        claimed_state: str = "claimed",
    ) -> dict[str, Any]:
        def _callback(session: Session, authenticated) -> dict[str, Any]:
            task = claim_next(
                session,
                actor_id=authenticated.actor.id,
                labels=labels,
                claim_states=claim_states,
                claimed_state=claimed_state,
            )
            if task is None:
                raise surface_error(
                    404,
                    "No matching job found",
                    details={
                        "labels": labels or [],
                        "claim_states": claim_states or [],
                    },
                )
            return task.model_dump(mode="json")

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="claim-next-job",
            callback=_callback,
            mutation=True,
        )

    registered.add("claim_next_job")

    @mcp.tool(
        name="release_job",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def release_job(
        job_id: uuid.UUID,
        token: str | None = None,
    ) -> dict[str, Any]:
        def _callback(session: Session, authenticated) -> dict[str, Any]:
            from agenticqueue_api.repo import release_claim

            released = release_claim(
                session,
                task_id=job_id,
                expected_actor_id=(
                    None
                    if authenticated.actor.actor_type == "admin"
                    else authenticated.actor.id
                ),
            )
            if released is None:
                raise surface_error(404, "Job not found or not releasable")
            return released.model_dump(mode="json")

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="release-job",
            callback=_callback,
            mutation=True,
        )

    registered.add("release_job")

    @mcp.tool(
        name="submit_payload",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def submit_payload(
        job_id: uuid.UUID,
        payload: dict[str, Any],
        token: str | None = None,
    ) -> dict[str, Any]:
        def _callback(session: Session, authenticated) -> dict[str, Any]:
            del session, authenticated
            raise surface_error(
                501,
                "submit_payload is not implemented yet on the MCP surface",
                error_code="not_implemented",
                details={"job_id": str(job_id), "payload_keys": sorted(payload.keys())},
            )

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="submit-payload",
            callback=_callback,
        )

    registered.add("submit_payload")

    @mcp.tool(
        name="submit_learning",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def submit_learning(
        task_id: uuid.UUID,
        learning_object: dict[str, Any],
        token: str | None = None,
    ) -> dict[str, Any]:
        return call_internal_api(
            app,
            method="POST",
            path="/v1/learnings/submit",
            token=token,
            json_body={
                "task_id": str(task_id),
                "learning_object": learning_object,
            },
        )

    registered.add("submit_learning")

    @mcp.tool(
        name="expire_learning",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def expire_learning(
        learning_id: uuid.UUID,
        token: str | None = None,
    ) -> dict[str, Any]:
        return call_internal_api(
            app,
            method="PATCH",
            path=f"/v1/learnings/{learning_id}",
            token=token,
            json_body={"status": "expired"},
        )

    registered.add("expire_learning")

    @mcp.tool(
        name="load_policy_pack",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def load_policy_pack(
        path: str,
        token: str | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        policy_path = Path(path)
        loaded = read_policy_pack(policy_path)
        policy_name = policy_path.name.removesuffix(".policy.yaml").removesuffix(
            ".yaml"
        )
        return call_internal_api(
            app,
            method="POST",
            path="/v1/policies",
            token=token,
            json_body={
                "workspace_id": None if workspace_id is None else str(workspace_id),
                "name": policy_name,
                "version": loaded.version,
                "hitl_required": loaded.hitl_required,
                "autonomy_tier": loaded.autonomy_tier,
                "capabilities": list(loaded.capabilities),
                "body": loaded.body,
            },
        )

    registered.add("load_policy_pack")

    @mcp.tool(
        name="attach_policy",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def attach_policy(
        pipeline_id: uuid.UUID,
        policy_id: uuid.UUID,
        token: str | None = None,
    ) -> dict[str, Any]:
        return call_internal_api(
            app,
            method="PATCH",
            path=f"/v1/projects/{pipeline_id}",
            token=token,
            json_body={"policy_id": str(policy_id)},
        )

    registered.add("attach_policy")

    @mcp.tool(
        name="query_graph",
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    def query_graph(
        entity_type: str,
        entity_id: uuid.UUID,
        token: str | None = None,
        hops: int = 1,
        edge_types: list[str] | None = None,
    ) -> dict[str, Any]:
        def _callback(session: Session, authenticated) -> dict[str, Any]:
            ensure_actor_has_capability(
                session,
                actor=authenticated.actor,
                capability=CapabilityKey.QUERY_GRAPH,
                required_scope={},
                entity_type=entity_type,
                entity_id=entity_id,
            )
            hits = neighbors(
                session,
                entity_type,
                entity_id,
                depth=hops,
                edge_types=edge_types,
            )
            return {"items": serialize_model(hits)}

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="query-graph",
            callback=_callback,
        )

    registered.add("query_graph")

    @mcp.tool(
        name="traverse_graph",
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    def traverse_graph(
        entity_type: str,
        entity_id: uuid.UUID,
        token: str | None = None,
        direction: str = "descendants",
        edge_types: list[str] | None = None,
        max_depth: int = 100,
    ) -> dict[str, Any]:
        def _callback(session: Session, authenticated) -> dict[str, Any]:
            ensure_actor_has_capability(
                session,
                actor=authenticated.actor,
                capability=CapabilityKey.QUERY_GRAPH,
                required_scope={},
                entity_type=entity_type,
                entity_id=entity_id,
            )
            if direction == "ancestors":
                hits = ancestors(
                    session,
                    entity_type,
                    entity_id,
                    edge_types=edge_types,
                    max_depth=max_depth,
                )
            elif direction == "descendants":
                hits = descendants(
                    session,
                    entity_type,
                    entity_id,
                    edge_types=edge_types,
                    max_depth=max_depth,
                )
            else:
                raise surface_error(
                    400,
                    "direction must be 'ancestors' or 'descendants'",
                )
            return {"items": serialize_model(hits), "direction": direction}

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="traverse-graph",
            callback=_callback,
        )

    registered.add("traverse_graph")

    @mcp.tool(
        name="search_surface",
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    def search_surface(
        tag: str,
        token: str | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        def _callback(session: Session, authenticated) -> dict[str, Any]:
            del session, authenticated
            raise surface_error(
                501,
                "search_surface is not implemented yet on the MCP surface",
                error_code="not_implemented",
                details={"tag": tag, "limit": limit},
            )

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="search-surface",
            callback=_callback,
        )

    registered.add("search_surface")

    @mcp.tool(
        name="supersede_decision",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def supersede_decision(
        decision_id: uuid.UUID,
        replaced_by: uuid.UUID,
        token: str | None = None,
    ) -> dict[str, Any]:
        return call_internal_api(
            app,
            method="POST",
            path="/v1/edges",
            token=token,
            json_body={
                "src_entity_type": "decision",
                "src_id": str(replaced_by),
                "dst_entity_type": "decision",
                "dst_id": str(decision_id),
                "relation": EdgeRelation.SUPERSEDES.value,
                "metadata": {},
            },
        )

    registered.add("supersede_decision")

    @mcp.tool(
        name="link_decision",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def link_decision(
        decision_id: uuid.UUID,
        job_id: uuid.UUID,
        token: str | None = None,
        relation: str = EdgeRelation.INFORMED_BY.value,
    ) -> dict[str, Any]:
        return call_internal_api(
            app,
            method="POST",
            path="/v1/edges",
            token=token,
            json_body={
                "src_entity_type": "decision",
                "src_id": str(decision_id),
                "dst_entity_type": "task",
                "dst_id": str(job_id),
                "relation": relation,
                "metadata": {},
            },
        )

    registered.add("link_decision")

    return registered
