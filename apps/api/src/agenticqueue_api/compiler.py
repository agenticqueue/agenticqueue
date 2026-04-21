"""Phase 3 packet compiler for coding-task context assembly."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
import uuid
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from pydantic import Field
from sqlalchemy.orm import Session

from agenticqueue_api.audit import set_session_redaction_context
from agenticqueue_api.config import (
    get_packet_scope_max_files,
    get_policies_dir,
    get_reload_enabled,
    get_repo_root,
    get_task_types_dir,
)
from agenticqueue_api.middleware.secret_redaction import (
    compile_secret_pattern_rules,
    payload_might_contain_secret,
    scan_json_payload,
)
from agenticqueue_api.models import (
    ArtifactModel,
    ArtifactRecord,
    DecisionModel,
    DecisionRecord,
    EdgeRecord,
    LearningModel,
    PolicyModel,
    PolicyRecord,
    ProjectRecord,
    TaskModel,
    TaskRecord,
    WorkspaceRecord,
)
from agenticqueue_api.models.edge import EdgeRelation
from agenticqueue_api.models.shared import SchemaModel
from agenticqueue_api.packet_versions import (
    get_packet_version_by_hash,
    packet_content_hash,
    packet_version_uuid,
    persist_packet_version,
)
from agenticqueue_api.policy.loader import PolicyPack, PolicyRegistry, load_policy_pack
from agenticqueue_api.policy.resolver import ResolvedPolicy, resolve_effective_policy
from agenticqueue_api.retrieval import RetrievalQuery, RetrievalService
from agenticqueue_api.repo import ancestors, neighbors
from agenticqueue_api.repo_scope import resolve_repo_scope
from agenticqueue_api.task_type_registry import TaskTypeDefinition, TaskTypeRegistry

if TYPE_CHECKING:
    from agenticqueue_api.packet_cache import PacketCache

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
PACKET_REDACTION_PATTERNS_KEY = "packet_redaction_patterns"
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
    redactions_count: int = 0
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
    session: Session,
    task: TaskRecord,
    task_definition: TaskTypeDefinition,
    policy_registry: PolicyRegistry,
) -> ResolvedPolicy:
    default_policy = policy_registry.get(DEFAULT_POLICY_NAME)
    task_type_policy = _task_type_policy_pack(str(task_definition.policy_path))
    base_policy = resolve_effective_policy(
        default_policy=default_policy,
        task_policy=task_type_policy,
    )
    project = session.get(ProjectRecord, task.project_id)
    workspace = (
        None if project is None else session.get(WorkspaceRecord, project.workspace_id)
    )
    workspace_policy = _attached_policy(
        session, workspace.policy_id if workspace else None
    )
    project_policy = _attached_policy(session, project.policy_id if project else None)
    task_policy = _attached_policy(session, task.policy_id)
    if workspace_policy is None and project_policy is None and task_policy is None:
        return base_policy
    return resolve_effective_policy(
        default_policy=base_policy,
        workspace_policy=workspace_policy,
        project_policy=project_policy,
        task_policy=task_policy,
    )


def resolve_task_policy(
    session: Session,
    task: TaskRecord,
    *,
    task_type_registry: TaskTypeRegistry,
    policy_registry: PolicyRegistry | None = None,
) -> ResolvedPolicy:
    """Resolve the effective policy for one task, including attachments."""

    task_definition = _load_task(task_type_registry, task.task_type)
    policies = policy_registry or _cached_policy_registry()
    return _resolved_policy(
        session=session,
        task=task,
        task_definition=task_definition,
        policy_registry=policies,
    )


def _attached_policy(
    session: Session,
    policy_id: uuid.UUID | None,
) -> PolicyModel | None:
    if policy_id is None:
        return None
    record = session.get(PolicyRecord, policy_id)
    if record is None:
        return None
    return PolicyModel.model_validate(record)


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


def _packet_redaction_context(
    *,
    redaction_count: object,
) -> dict[str, Any] | None:
    if not isinstance(redaction_count, int) or redaction_count < 1:
        return None
    return {
        "redaction_count": redaction_count,
        "source": "packet",
    }


def _packet_redaction_probe_payload(packet: PacketV1) -> dict[str, Any]:
    return {
        "task_description": packet.task.description,
        "task_contract": packet.task_contract,
        "relevant_decisions": [
            decision.model_dump(mode="json") for decision in packet.relevant_decisions
        ],
        "relevant_learnings": [
            learning.model_dump(mode="json") for learning in packet.relevant_learnings
        ],
        "linked_artifacts": [
            artifact.model_dump(mode="json") for artifact in packet.linked_artifacts
        ],
    }


def _apply_packet_redaction(
    packet: PacketV1,
    *,
    policy: ResolvedPolicy,
) -> tuple[PacketV1, dict[str, Any] | None]:
    extra_rules = compile_secret_pattern_rules(
        policy.body.get(PACKET_REDACTION_PATTERNS_KEY)
    )
    if not payload_might_contain_secret(
        _packet_redaction_probe_payload(packet),
        extra_rules=extra_rules,
    ):
        packet.redactions_count = 0
        return packet, None

    scan = scan_json_payload(
        packet.model_dump(mode="json"),
        hard_block=False,
        extra_rules=extra_rules,
    )
    payload = dict(scan.sanitized_payload)
    payload["redactions_count"] = scan.redaction_count
    redaction = _packet_redaction_context(redaction_count=scan.redaction_count)
    if redaction is not None:
        redaction["types_matched"] = list(scan.types_matched)
    return PacketV1.model_validate(payload), redaction


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
        session=session,
        task=task,
        task_definition=task_definition,
        policy_registry=policies,
    )

    retrieval_result = RetrievalService(session).retrieve(
        RetrievalQuery(
            task_id=task.id,
            k=learning_limit,
            fuzzy_global_search=bool(
                resolved_policy.body.get("enable_fuzzy_global_search", False)
            ),
        )
    )
    relevant_learnings = retrieval_result.items
    retrieval_tiers_used = retrieval_result.tiers_fired

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
    packet, redaction_context = _apply_packet_redaction(
        packet,
        policy=resolved_policy,
    )
    set_session_redaction_context(session, redaction=redaction_context)

    packet_hash = packet_content_hash(packet)
    packet.packet_version_id = str(packet_version_uuid(packet_hash))
    return packet


def get_packet_by_hash(session: Session, packet_hash: str) -> dict[str, Any] | None:
    """Return a previously persisted packet by content hash."""

    packet_version = get_packet_version_by_hash(session, packet_hash)
    if packet_version is None:
        return None
    return dict(packet_version.payload)


def compile_packet(
    session: Session,
    task_id: uuid.UUID,
    *,
    learning_limit: int = DEFAULT_LEARNING_LIMIT,
    task_type_registry: TaskTypeRegistry | None = None,
    policy_registry: PolicyRegistry | None = None,
    packet_cache: PacketCache | None = None,
) -> dict[str, Any]:
    """Return the compiled packet as a JSON-serializable dict."""

    cache_enabled = (
        packet_cache is not None
        and task_type_registry is None
        and policy_registry is None
    )
    if cache_enabled:
        assert packet_cache is not None
        cached = packet_cache.get(task_id, learning_limit=learning_limit)
        if cached is not None:
            set_session_redaction_context(
                session,
                redaction=_packet_redaction_context(
                    redaction_count=cached.get("redactions_count")
                ),
            )
            packet_cache.schedule_prefetch(task_id, learning_limit=learning_limit)
            return cached

    packet = assemble_packet(
        session,
        task_id,
        learning_limit=learning_limit,
        task_type_registry=task_type_registry,
        policy_registry=policy_registry,
    )
    packet_version = persist_packet_version(session, task_id, packet)
    payload = dict(packet_version.payload)
    if cache_enabled:
        assert packet_cache is not None
        packet_cache.put(
            session,
            task_id,
            payload,
            learning_limit=learning_limit,
        )
        packet_cache.schedule_prefetch(task_id, learning_limit=learning_limit)
    return payload


__all__ = [
    "PacketPermissions",
    "PacketRepoScope",
    "PacketV1",
    "assemble_packet",
    "compile_packet",
    "get_packet_by_hash",
    "packet_decisions",
]
