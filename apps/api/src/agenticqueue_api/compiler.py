"""Phase 3 packet compiler for coding-task context assembly."""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import uuid
from typing import Any

import sqlalchemy as sa
from pydantic import Field
from sqlalchemy.orm import Session

from agenticqueue_api.config import (
    get_packet_scope_max_files,
    get_policies_dir,
    get_reload_enabled,
    get_repo_root,
    get_task_types_dir,
)
from agenticqueue_api.learnings import LearningDedupeService, rank_learnings_for_task
from agenticqueue_api.learnings.dedupe import cosine_similarity
from agenticqueue_api.models import (
    ArtifactModel,
    ArtifactRecord,
    DecisionModel,
    DecisionRecord,
    EdgeRecord,
    LearningModel,
    LearningRecord,
    TaskModel,
    TaskRecord,
)
from agenticqueue_api.models.edge import EdgeRelation
from agenticqueue_api.models.shared import SchemaModel
from agenticqueue_api.policy.loader import PolicyPack, PolicyRegistry, load_policy_pack
from agenticqueue_api.policy.resolver import ResolvedPolicy, resolve_effective_policy
from agenticqueue_api.repo import ancestors, neighbors
from agenticqueue_api.repo_scope import resolve_repo_scope
from agenticqueue_api.schemas.learning import LearningStatus
from agenticqueue_api.task_type_registry import TaskTypeDefinition, TaskTypeRegistry

DEFAULT_LEARNING_LIMIT = 5
DEFAULT_PACKET_DECISION_MAX_HOPS = 3
DEFAULT_PACKET_DECISION_MAX_NODES = 25
DEFAULT_POLICY_NAME = "default-coding"
PACKET_DECISION_TASK_EDGE_TYPES = (EdgeRelation.DEPENDS_ON,)
PACKET_DECISION_NEIGHBOR_EDGE_TYPES = (
    EdgeRelation.INFORMED_BY,
    EdgeRelation.IMPLEMENTS,
    EdgeRelation.SUPERSEDES,
)
OPEN_QUESTIONS_HEADING_RE = re.compile(r"^#{2,6}\s+Open Questions\s*$")
BULLET_ITEM_RE = re.compile(r"^\s*[-*]\s+(?P<item>.+?)\s*$")


class PacketRepoScope(SchemaModel):
    """Repository scope included in the compiled packet."""

    repo: str
    branch: str
    file_scope: list[str] = Field(default_factory=list)
    surface_area: list[str] = Field(default_factory=list)
    estimated_token_count: int = 0


class PacketPermissions(SchemaModel):
    """Resolved permissions for one packet."""

    policy_name: str
    policy_version: str
    source: str
    hitl_required: bool
    autonomy_tier: int
    validation_mode: str
    capabilities: list[str] = Field(default_factory=list)


class PacketV1(SchemaModel):
    """Compiled packet payload returned to an agent."""

    task: TaskModel
    task_contract: dict[str, Any] = Field(default_factory=dict)
    definition_of_done: list[str] = Field(default_factory=list)
    relevant_decisions: list[DecisionModel] = Field(default_factory=list)
    relevant_learnings: list[LearningModel] = Field(default_factory=list)
    linked_artifacts: list[ArtifactModel] = Field(default_factory=list)
    repo_scope: PacketRepoScope
    open_questions: list[str] = Field(default_factory=list)
    permissions: PacketPermissions
    expected_output_schema: dict[str, Any] = Field(default_factory=dict)
    packet_version_id: str
    retrieval_tiers_used: list[str] = Field(default_factory=list)


@lru_cache(maxsize=1)
def _cached_task_type_registry() -> TaskTypeRegistry:
    registry = TaskTypeRegistry(
        get_task_types_dir(),
        reload_enabled=get_reload_enabled(),
    )
    registry.load()
    return registry


@lru_cache(maxsize=1)
def _cached_policy_registry() -> PolicyRegistry:
    registry = PolicyRegistry(get_policies_dir())
    registry.load()
    return registry


@lru_cache(maxsize=None)
def _task_type_policy_pack(path: str) -> PolicyPack:
    return load_policy_pack(Path(path))


def _normalize_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if isinstance(item, str):
            normalized = item.strip()
            if normalized:
                items.append(normalized)
    return items


def _normalize_string(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _task_repo_scope(task: TaskRecord) -> PacketRepoScope:
    contract = task.contract or {}
    resolved_scope = resolve_repo_scope(
        get_repo_root(),
        _normalize_string_list(contract.get("file_scope")),
        max_files=get_packet_scope_max_files(),
    )
    return PacketRepoScope(
        repo=_normalize_string(contract.get("repo")),
        branch=_normalize_string(contract.get("branch")),
        file_scope=resolved_scope.file_scope,
        surface_area=_normalize_string_list(contract.get("surface_area")),
        estimated_token_count=resolved_scope.estimated_token_count,
    )


def _extract_open_questions(task: TaskRecord) -> list[str]:
    contract = task.contract or {}
    content = [
        value
        for value in (
            _normalize_string(contract.get("spec")),
            _normalize_string(task.description),
        )
        if value
    ]
    lines = "\n".join(content).splitlines()

    questions: list[str] = []
    collecting = False
    for line in lines:
        if OPEN_QUESTIONS_HEADING_RE.match(line.strip()):
            collecting = True
            continue
        if collecting and line.lstrip().startswith("#"):
            break
        if not collecting:
            continue
        match = BULLET_ITEM_RE.match(line)
        if match is None:
            continue
        questions.append(match.group("item").strip())
    return questions


def _load_task(
    task_type_registry: TaskTypeRegistry, task_type: str
) -> TaskTypeDefinition:
    return task_type_registry.get(task_type)


def _resolved_policy(
    *,
    task_definition: TaskTypeDefinition,
    policy_registry: PolicyRegistry,
) -> ResolvedPolicy:
    default_policy = policy_registry.get(DEFAULT_POLICY_NAME)
    task_type_policy = _task_type_policy_pack(str(task_definition.policy_path))
    return resolve_effective_policy(
        default_policy=default_policy,
        task_policy=task_type_policy,
    )


def _packet_permissions(policy: ResolvedPolicy) -> PacketPermissions:
    return PacketPermissions(
        policy_name=policy.name,
        policy_version=policy.version,
        source=policy.source,
        hitl_required=policy.hitl_required,
        autonomy_tier=policy.autonomy_tier,
        validation_mode=policy.validation_mode,
        capabilities=[capability.value for capability in policy.capabilities],
    )


def _expected_output_schema(definition: TaskTypeDefinition) -> dict[str, Any]:
    properties = definition.schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    output_schema = properties.get("output")
    if not isinstance(output_schema, dict):
        return {}
    return output_schema


def _anchor_task_ids(
    session: Session,
    task_id: uuid.UUID,
    *,
    max_hops: int,
) -> set[uuid.UUID]:
    task_ids = {task_id}
    hits = ancestors(
        session,
        "task",
        task_id,
        edge_types=PACKET_DECISION_TASK_EDGE_TYPES,
        max_depth=max_hops,
    )
    task_ids.update(hit.entity_id for hit in hits if hit.entity_type == "task")
    return task_ids


def packet_decisions(
    session: Session,
    task_id: uuid.UUID,
    *,
    max_hops: int = DEFAULT_PACKET_DECISION_MAX_HOPS,
    max_nodes: int = DEFAULT_PACKET_DECISION_MAX_NODES,
) -> list[DecisionModel]:
    """Return graph-relevant decisions for one task, newest first."""

    if max_hops < 1:
        raise ValueError("max_hops must be at least 1")
    if max_nodes < 1:
        raise ValueError("max_nodes must be at least 1")

    task = session.get(TaskRecord, task_id)
    if task is None:
        raise KeyError(str(task_id))

    decision_ids = set(
        session.scalars(
            sa.select(DecisionRecord.id).where(DecisionRecord.task_id == task.id)
        )
    )
    has_dependency_ancestors = session.scalar(
        sa.select(EdgeRecord.id)
        .where(EdgeRecord.dst_entity_type == "task")
        .where(EdgeRecord.dst_id == task.id)
        .where(EdgeRecord.relation.in_(PACKET_DECISION_TASK_EDGE_TYPES))
        .limit(1)
    )
    if has_dependency_ancestors is None and not decision_ids:
        return []

    if has_dependency_ancestors is not None:
        anchor_task_ids = _anchor_task_ids(session, task.id, max_hops=max_hops)
        decision_ids.update(
            session.scalars(
                sa.select(DecisionRecord.id).where(
                    DecisionRecord.task_id.in_(anchor_task_ids)
                )
            )
        )
    if not decision_ids:
        return []

    for decision_id in tuple(decision_ids):
        hits = neighbors(
            session,
            "decision",
            decision_id,
            depth=max_hops,
            edge_types=PACKET_DECISION_NEIGHBOR_EDGE_TYPES,
        )
        decision_ids.update(
            hit.entity_id for hit in hits if hit.entity_type == "decision"
        )

    rows = session.scalars(
        sa.select(DecisionRecord)
        .where(DecisionRecord.id.in_(decision_ids))
        .order_by(DecisionRecord.decided_at.desc(), DecisionRecord.id.desc())
        .limit(max_nodes)
    )
    return [DecisionModel.model_validate(record) for record in rows]


def _relevant_decisions(session: Session, task: TaskRecord) -> list[DecisionModel]:
    decisions = packet_decisions(session, task.id)
    if not decisions:
        return []
    return decisions


def _linked_artifacts(session: Session, task: TaskRecord) -> list[ArtifactModel]:
    rows = session.scalars(
        sa.select(ArtifactRecord)
        .where(ArtifactRecord.task_id == task.id)
        .order_by(ArtifactRecord.created_at.asc(), ArtifactRecord.id.asc())
    )
    return [ArtifactModel.model_validate(record) for record in rows]


def _learning_similarity_text(learning: LearningRecord) -> str:
    return "\n".join(
        value
        for value in (
            learning.title,
            learning.action_rule,
            learning.what_happened,
            learning.what_learned,
            *learning.evidence,
        )
        if value
    )


def _task_similarity_text(task: TaskRecord) -> str:
    contract = task.contract or {}
    return "\n".join(
        value
        for value in (
            task.task_type,
            task.title,
            _normalize_string(task.description),
            _normalize_string(contract.get("spec")),
            *_normalize_string_list(contract.get("file_scope")),
            *_normalize_string_list(contract.get("surface_area")),
        )
        if value
    )


def _vector_fallback_learnings(
    session: Session,
    task: TaskRecord,
    *,
    exclude_ids: set[uuid.UUID],
    limit: int,
) -> list[LearningModel]:
    dedupe = LearningDedupeService(session)
    task_embedding = dedupe.embed_text(_task_similarity_text(task))

    candidates: list[tuple[float, LearningRecord]] = []
    rows = session.scalars(
        sa.select(LearningRecord)
        .where(LearningRecord.status == LearningStatus.ACTIVE.value)
        .order_by(LearningRecord.created_at.asc(), LearningRecord.id.asc())
    )
    for record in rows:
        if record.id in exclude_ids:
            continue
        similarity = cosine_similarity(
            task_embedding,
            dedupe.embed_text(_learning_similarity_text(record)),
        )
        if similarity <= 0.0:
            continue
        candidates.append((similarity, record))

    candidates.sort(
        key=lambda item: (-item[0], item[1].created_at, str(item[1].id)),
    )
    return [LearningModel.model_validate(record) for _, record in candidates[:limit]]


def _packet_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assemble_packet(
    session: Session,
    task_id: uuid.UUID,
    *,
    learning_limit: int = DEFAULT_LEARNING_LIMIT,
    task_type_registry: TaskTypeRegistry | None = None,
    policy_registry: PolicyRegistry | None = None,
) -> PacketV1:
    """Compile one task into the Phase 3 packet shape."""

    task = session.get(TaskRecord, task_id)
    if task is None:
        raise KeyError(str(task_id))

    registry = task_type_registry or _cached_task_type_registry()
    policies = policy_registry or _cached_policy_registry()
    task_definition = _load_task(registry, task.task_type)
    resolved_policy = _resolved_policy(
        task_definition=task_definition,
        policy_registry=policies,
    )

    relevant_learnings = rank_learnings_for_task(session, task.id, k=learning_limit)
    retrieval_tiers_used = ["graph", "surface"]

    fuzzy_global_search = bool(
        resolved_policy.body.get("enable_fuzzy_global_search", False)
    )
    if fuzzy_global_search and len(relevant_learnings) < learning_limit:
        vector_learnings = _vector_fallback_learnings(
            session,
            task,
            exclude_ids={learning.id for learning in relevant_learnings},
            limit=learning_limit - len(relevant_learnings),
        )
        relevant_learnings = [*relevant_learnings, *vector_learnings]
        retrieval_tiers_used.append("vector")

    packet = PacketV1(
        task=TaskModel.model_validate(task),
        task_contract=dict(task.contract or {}),
        definition_of_done=list(task.definition_of_done or []),
        relevant_decisions=_relevant_decisions(session, task),
        relevant_learnings=relevant_learnings,
        linked_artifacts=_linked_artifacts(session, task),
        repo_scope=_task_repo_scope(task),
        open_questions=_extract_open_questions(task),
        permissions=_packet_permissions(resolved_policy),
        expected_output_schema=_expected_output_schema(task_definition),
        packet_version_id="",
        retrieval_tiers_used=retrieval_tiers_used,
    )

    packet.packet_version_id = _packet_hash(
        packet.model_dump(mode="json", exclude={"packet_version_id"})
    )
    return packet


def compile_packet(
    session: Session,
    task_id: uuid.UUID,
    *,
    learning_limit: int = DEFAULT_LEARNING_LIMIT,
    task_type_registry: TaskTypeRegistry | None = None,
    policy_registry: PolicyRegistry | None = None,
) -> dict[str, Any]:
    """Return the compiled packet as a JSON-serializable dict."""

    return assemble_packet(
        session,
        task_id,
        learning_limit=learning_limit,
        task_type_registry=task_type_registry,
        policy_registry=policy_registry,
    ).model_dump(mode="json")


__all__ = [
    "PacketPermissions",
    "PacketRepoScope",
    "PacketV1",
    "assemble_packet",
    "compile_packet",
    "packet_decisions",
]
