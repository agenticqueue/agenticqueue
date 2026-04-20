"""Graph traversal helpers for Phase 1 edge queries."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Collection
import uuid
from typing import Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.dialects.postgresql import array as pg_array
from sqlalchemy.orm import Session, aliased

from agenticqueue_api.models import (
    EdgeRecord,
    EdgeRelation,
    LearningModel,
    LearningRecord,
)
from agenticqueue_api.schemas.learning import LearningStatus

DEFAULT_MAX_DEPTH = 100
DOWNSTREAM_DECISION_ENTITY_TYPES = frozenset({"task", "artifact", "run"})
LEARNING_SCOPE_FILTERS: dict[str, tuple[str, ...] | None] = {
    "all": None,
    "task": ("task",),
    "project": ("task", "project"),
    "global": ("task", "project", "global"),
}


class CycleError(ValueError):
    """Raised when a depends_on edge would create a cycle."""


class GraphEntityRef(BaseModel):
    """Typed reference to a graph node."""

    model_config = ConfigDict(frozen=True)

    entity_type: str
    entity_id: uuid.UUID


class GraphTraversalHit(GraphEntityRef):
    """Traversal result with hop distance from the root."""

    depth: int


class GraphPath(BaseModel):
    """Shortest directed path between two graph nodes."""

    model_config = ConfigDict(frozen=True)

    nodes: list[GraphEntityRef]
    relations: list[EdgeRelation]


def _normalize_entity_type(entity_type: str) -> str:
    normalized = entity_type.strip()
    if not normalized:
        raise ValueError("entity_type must not be empty")
    return normalized


def _normalize_edge_types(
    edge_types: Collection[EdgeRelation | str] | None,
) -> tuple[EdgeRelation, ...] | None:
    if edge_types is None:
        return None
    return tuple(
        edge_type if isinstance(edge_type, EdgeRelation) else EdgeRelation(edge_type)
        for edge_type in edge_types
    )


def _validate_max_depth(max_depth: int) -> None:
    if max_depth < 1:
        raise ValueError("max_depth must be at least 1")


def _normalize_learning_scope(scope: str) -> tuple[str, ...] | None:
    normalized = scope.strip().lower()
    if normalized not in LEARNING_SCOPE_FILTERS:
        raise ValueError("scope must be one of all | task | project | global")
    return LEARNING_SCOPE_FILTERS[normalized]


def _active_edge_condition(edge_record: Any) -> sa.ColumnElement[bool]:
    metadata = edge_record.edge_metadata
    return sa.and_(
        metadata["superseded_at"].astext.is_(None),
        metadata["superseded_by"].astext.is_(None),
        sa.or_(
            metadata["status"].astext.is_(None),
            metadata["status"].astext != "superseded",
        ),
        sa.or_(
            metadata["is_active"].astext.is_(None),
            metadata["is_active"].astext != "false",
        ),
    )


def _node_key_literal(entity_type: str, entity_id: uuid.UUID) -> sa.ColumnElement[str]:
    return sa.literal(f"{entity_type}:{entity_id}", type_=sa.String())


def _node_key_expr(
    entity_type_column: Any,
    entity_id_column: Any,
) -> sa.ColumnElement[str]:
    return sa.func.concat(
        entity_type_column,
        sa.literal(":"),
        sa.cast(entity_id_column, sa.String()),
    )


def _node_path_array(values: list[Any]) -> Any:
    return pg_array(values, type_=sa.String())  # type: ignore[misc]


def _walk_direction(
    session: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    edge_types: Collection[EdgeRelation | str] | None,
    max_depth: int,
    outbound: bool,
) -> list[GraphTraversalHit]:
    _validate_max_depth(max_depth)
    normalized_entity_type = _normalize_entity_type(entity_type)
    normalized_edge_types = _normalize_edge_types(edge_types)

    base_edge = aliased(EdgeRecord)
    if outbound:
        base_match = sa.and_(
            base_edge.src_entity_type == normalized_entity_type,
            base_edge.src_id == entity_id,
        )
        base_next_type = base_edge.dst_entity_type
        base_next_id = base_edge.dst_id
    else:
        base_match = sa.and_(
            base_edge.dst_entity_type == normalized_entity_type,
            base_edge.dst_id == entity_id,
        )
        base_next_type = base_edge.src_entity_type
        base_next_id = base_edge.src_id

    base_select = (
        sa.select(
            base_next_type.label("entity_type"),
            base_next_id.label("entity_id"),
            sa.literal(1).label("depth"),
            _node_path_array(
                [
                    _node_key_literal(normalized_entity_type, entity_id),
                    _node_key_expr(base_next_type, base_next_id),
                ]
            ).label("path"),
        )
        .where(base_match)
        .where(_active_edge_condition(base_edge))
    )
    if normalized_edge_types is not None:
        base_select = base_select.where(base_edge.relation.in_(normalized_edge_types))

    walk = base_select.cte(name="graph_walk", recursive=True)

    recursive_edge = aliased(EdgeRecord)
    if outbound:
        recursive_match = sa.and_(
            recursive_edge.src_entity_type == walk.c.entity_type,
            recursive_edge.src_id == walk.c.entity_id,
        )
        recursive_next_type = recursive_edge.dst_entity_type
        recursive_next_id = recursive_edge.dst_id
    else:
        recursive_match = sa.and_(
            recursive_edge.dst_entity_type == walk.c.entity_type,
            recursive_edge.dst_id == walk.c.entity_id,
        )
        recursive_next_type = recursive_edge.src_entity_type
        recursive_next_id = recursive_edge.src_id

    next_key = _node_key_expr(recursive_next_type, recursive_next_id)
    recursive_select = (
        sa.select(
            recursive_next_type.label("entity_type"),
            recursive_next_id.label("entity_id"),
            (walk.c.depth + 1).label("depth"),
            sa.func.array_append(walk.c.path, next_key).label("path"),
        )
        .select_from(walk)
        .join(recursive_edge, recursive_match)
        .where(walk.c.depth < max_depth)
        .where(_active_edge_condition(recursive_edge))
        .where(sa.func.array_position(walk.c.path, next_key).is_(None))
    )
    if normalized_edge_types is not None:
        recursive_select = recursive_select.where(
            recursive_edge.relation.in_(normalized_edge_types),
        )

    walk = walk.union_all(recursive_select)

    min_depth = sa.func.min(walk.c.depth).label("depth")
    rows = session.execute(
        sa.select(
            walk.c.entity_type,
            walk.c.entity_id,
            min_depth,
        )
        .group_by(walk.c.entity_type, walk.c.entity_id)
        .order_by(min_depth.asc(), walk.c.entity_type.asc(), walk.c.entity_id.asc()),
    ).all()

    return [
        GraphTraversalHit(
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            depth=row.depth,
        )
        for row in rows
    ]


def neighbors(
    session: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    *,
    depth: int = 1,
    edge_types: Collection[EdgeRelation | str] | None = None,
) -> list[GraphTraversalHit]:
    outbound_hits = _walk_direction(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        edge_types=edge_types,
        max_depth=depth,
        outbound=True,
    )
    inbound_hits = _walk_direction(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        edge_types=edge_types,
        max_depth=depth,
        outbound=False,
    )

    deduped: dict[tuple[str, uuid.UUID], GraphTraversalHit] = {}
    for hit in sorted(
        [*outbound_hits, *inbound_hits],
        key=lambda value: (value.depth, value.entity_type, str(value.entity_id)),
    ):
        deduped.setdefault((hit.entity_type, hit.entity_id), hit)
    return list(deduped.values())


def ancestors(
    session: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    *,
    edge_types: Collection[EdgeRelation | str] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> list[GraphTraversalHit]:
    return _walk_direction(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        edge_types=edge_types,
        max_depth=max_depth,
        outbound=False,
    )


def descendants(
    session: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    *,
    edge_types: Collection[EdgeRelation | str] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> list[GraphTraversalHit]:
    return _walk_direction(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        edge_types=edge_types,
        max_depth=max_depth,
        outbound=True,
    )


def shortest_path(
    session: Session,
    src_entity_type: str,
    src_entity_id: uuid.UUID,
    dst_entity_type: str,
    dst_entity_id: uuid.UUID,
    *,
    edge_types: Collection[EdgeRelation | str] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> GraphPath | None:
    _validate_max_depth(max_depth)
    normalized_edge_types = _normalize_edge_types(edge_types)
    start_ref = GraphEntityRef(
        entity_type=_normalize_entity_type(src_entity_type),
        entity_id=src_entity_id,
    )
    target_key = (_normalize_entity_type(dst_entity_type), dst_entity_id)

    statement = (
        sa.select(
            EdgeRecord.src_entity_type,
            EdgeRecord.src_id,
            EdgeRecord.dst_entity_type,
            EdgeRecord.dst_id,
            EdgeRecord.relation,
        )
        .where(_active_edge_condition(EdgeRecord))
        .order_by(
            EdgeRecord.created_at.asc(),
            EdgeRecord.id.asc(),
        )
    )
    if normalized_edge_types is not None:
        statement = statement.where(EdgeRecord.relation.in_(normalized_edge_types))

    adjacency: dict[
        tuple[str, uuid.UUID], list[tuple[GraphEntityRef, EdgeRelation]]
    ] = defaultdict(list)
    for row in session.execute(statement):
        adjacency[(row.src_entity_type, row.src_id)].append(
            (
                GraphEntityRef(
                    entity_type=row.dst_entity_type,
                    entity_id=row.dst_id,
                ),
                row.relation,
            ),
        )

    queue: deque[tuple[GraphEntityRef, list[GraphEntityRef], list[EdgeRelation]]] = (
        deque([(start_ref, [start_ref], [])])
    )
    visited: dict[tuple[str, uuid.UUID], int] = {
        (start_ref.entity_type, start_ref.entity_id): 0,
    }

    while queue:
        current_ref, node_path, relation_path = queue.popleft()
        if len(relation_path) >= max_depth:
            continue

        for next_ref, relation in adjacency[
            (current_ref.entity_type, current_ref.entity_id)
        ]:
            next_key = (next_ref.entity_type, next_ref.entity_id)
            next_depth = len(relation_path) + 1
            prior_depth = visited.get(next_key)
            if prior_depth is not None and prior_depth <= next_depth:
                continue

            next_node_path = [*node_path, next_ref]
            next_relation_path = [*relation_path, relation]
            if next_key == target_key:
                return GraphPath(nodes=next_node_path, relations=next_relation_path)

            visited[next_key] = next_depth
            queue.append((next_ref, next_node_path, next_relation_path))

    return None


def downstream_of_decision(
    session: Session,
    decision_id: uuid.UUID,
    *,
    edge_types: Collection[EdgeRelation | str] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> list[GraphTraversalHit]:
    hits = descendants(
        session,
        "decision",
        decision_id,
        edge_types=edge_types,
        max_depth=max_depth,
    )
    return [hit for hit in hits if hit.entity_type in DOWNSTREAM_DECISION_ENTITY_TYPES]


def learnings_for(
    session: Session,
    entity_id: uuid.UUID,
    *,
    scope: Literal["all", "task", "project", "global"] = "all",
    include_inactive: bool = False,
) -> list[LearningModel]:
    scope_filter = _normalize_learning_scope(scope)
    edge = aliased(EdgeRecord)
    learning = aliased(LearningRecord)

    statement = (
        sa.select(learning)
        .join(
            edge,
            sa.or_(
                sa.and_(
                    edge.src_entity_type == "learning",
                    edge.src_id == learning.id,
                    edge.dst_id == entity_id,
                ),
                sa.and_(
                    edge.dst_entity_type == "learning",
                    edge.dst_id == learning.id,
                    edge.src_id == entity_id,
                ),
            ),
        )
        .where(edge.relation == EdgeRelation.LEARNED_FROM)
        .where(_active_edge_condition(edge))
        .order_by(learning.created_at.asc(), learning.id.asc())
    )
    if not include_inactive:
        statement = statement.where(learning.status == LearningStatus.ACTIVE.value)
    if scope_filter is not None:
        statement = statement.where(learning.scope.in_(scope_filter))

    return [
        LearningModel.model_validate(record)
        for record in session.scalars(statement).unique().all()
    ]


def ensure_dependency_edge_is_acyclic(
    session: Session,
    *,
    src_entity_type: str,
    src_entity_id: uuid.UUID,
    dst_entity_type: str,
    dst_entity_id: uuid.UUID,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> None:
    normalized_src_type = _normalize_entity_type(src_entity_type)
    normalized_dst_type = _normalize_entity_type(dst_entity_type)

    if normalized_src_type == normalized_dst_type and src_entity_id == dst_entity_id:
        raise CycleError("depends_on edges cannot reference the same node twice")

    cycle_path = shortest_path(
        session,
        normalized_dst_type,
        dst_entity_id,
        normalized_src_type,
        src_entity_id,
        edge_types=(EdgeRelation.DEPENDS_ON,),
        max_depth=max_depth,
    )
    if cycle_path is None:
        return

    cycle_nodes = [
        GraphEntityRef(entity_type=normalized_src_type, entity_id=src_entity_id),
        *cycle_path.nodes,
    ]
    cycle_text = " -> ".join(
        f"{node.entity_type}:{node.entity_id}" for node in cycle_nodes
    )
    raise CycleError(
        f"depends_on edges must remain acyclic; proposed edge closes {cycle_text}"
    )
