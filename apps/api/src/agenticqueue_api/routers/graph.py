"""Read-only graph and surface-search REST routes for the public transport layer."""

from __future__ import annotations

import uuid
from typing import Any, cast

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from agenticqueue_api.capabilities import ensure_actor_has_capability
from agenticqueue_api.db import graph_timeout
from agenticqueue_api.errors import raise_api_error
from agenticqueue_api.models import ActorModel, CapabilityKey, TaskRecord
from agenticqueue_api.models.shared import SchemaModel
from agenticqueue_api.repo import GraphTraversalHit, ancestors, descendants, neighbors


class GraphHitsResponse(SchemaModel):
    """Traversal payload shared by the graph read endpoints."""

    items: list[GraphTraversalHit]
    direction: str | None = None


class GraphSurfaceHit(SchemaModel):
    """One entity matched by a surface-area search."""

    entity_type: str
    entity_id: uuid.UUID
    matched_tags: list[str]


class GraphSurfaceResponse(SchemaModel):
    """Surface-area search payload."""

    items: list[GraphSurfaceHit]


def _require_request_auth(request: Request) -> ActorModel:
    actor = getattr(request.state, "actor", None)
    if not isinstance(actor, ActorModel):
        raise_api_error(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")
    return actor


def _ensure_query_graph_capability(
    session: Session,
    *,
    actor: ActorModel,
    entity_type: str,
    entity_id: uuid.UUID | None,
) -> None:
    ensure_actor_has_capability(
        session,
        actor=actor,
        capability=CapabilityKey.QUERY_GRAPH,
        required_scope={},
        entity_type=entity_type,
        entity_id=entity_id,
    )


def _matching_surface_tags(contract: dict[str, Any], tag: str) -> list[str]:
    raw_surface_area = contract.get("surface_area")
    if not isinstance(raw_surface_area, list):
        return []
    normalized_tag = tag.strip().lower()
    matches = [
        value.strip()
        for value in raw_surface_area
        if isinstance(value, str)
        and value.strip()
        and normalized_tag in value.strip().lower()
    ]
    return list(dict.fromkeys(matches))


def build_graph_router(get_db_session: Any) -> APIRouter:
    """Build the public graph/surface REST routes."""

    router = APIRouter()

    @router.get(
        "/v1/graph/neighborhood/{entity_id}",
        response_model=GraphHitsResponse,
    )
    def graph_neighborhood_endpoint(
        entity_id: uuid.UUID,
        request: Request,
        entity_type: str = Query(default="task", min_length=1),
        hops: int = Query(default=1, ge=1, le=10),
        edge: list[str] | None = Query(default=None),
        session: Session = Depends(get_db_session),
    ) -> GraphHitsResponse:
        actor = _require_request_auth(request)
        _ensure_query_graph_capability(
            session,
            actor=actor,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        with graph_timeout(session, endpoint="v1.graph.neighborhood"):
            hits = neighbors(
                session,
                entity_type,
                entity_id,
                depth=hops,
                edge_types=edge,
            )
        return GraphHitsResponse(items=hits)

    @router.get(
        "/v1/graph/traverse/{entity_id}",
        response_model=GraphHitsResponse,
    )
    def graph_traverse_endpoint(
        entity_id: uuid.UUID,
        request: Request,
        entity_type: str = Query(default="task", min_length=1),
        direction: str = Query(default="descendants"),
        edge: list[str] | None = Query(default=None),
        max_depth: int = Query(default=100, ge=1, le=100),
        session: Session = Depends(get_db_session),
    ) -> GraphHitsResponse:
        actor = _require_request_auth(request)
        _ensure_query_graph_capability(
            session,
            actor=actor,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        with graph_timeout(session, endpoint="v1.graph.traverse"):
            if direction == "ancestors":
                hits = ancestors(
                    session,
                    entity_type,
                    entity_id,
                    edge_types=edge,
                    max_depth=max_depth,
                )
            elif direction == "descendants":
                hits = descendants(
                    session,
                    entity_type,
                    entity_id,
                    edge_types=edge,
                    max_depth=max_depth,
                )
            else:
                raise_api_error(
                    status.HTTP_400_BAD_REQUEST,
                    "direction must be 'ancestors' or 'descendants'",
                    details={"direction": direction},
                )
        return GraphHitsResponse(items=hits, direction=direction)

    @router.get(
        "/v1/graph/surface",
        response_model=GraphSurfaceResponse,
    )
    def graph_surface_endpoint(
        request: Request,
        tag: str = Query(min_length=1),
        limit: int = Query(default=25, ge=1, le=100),
        session: Session = Depends(get_db_session),
    ) -> GraphSurfaceResponse:
        actor = _require_request_auth(request)
        _ensure_query_graph_capability(
            session,
            actor=actor,
            entity_type="task",
            entity_id=None,
        )

        statement = (
            sa.select(TaskRecord)
            .order_by(TaskRecord.created_at.desc(), TaskRecord.id.asc())
            .limit(limit * 4)
        )
        rows = session.scalars(statement).all()

        items: list[GraphSurfaceHit] = []
        for task in rows:
            matches = _matching_surface_tags(
                cast(dict[str, Any], task.contract or {}), tag
            )
            if not matches:
                continue
            items.append(
                GraphSurfaceHit(
                    entity_type="task",
                    entity_id=task.id,
                    matched_tags=matches,
                )
            )
            if len(items) >= limit:
                break

        return GraphSurfaceResponse(items=items)

    return router


__all__ = ["GraphHitsResponse", "GraphSurfaceResponse", "build_graph_router"]
