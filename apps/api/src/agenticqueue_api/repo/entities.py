"""Create/read helpers for AgenticQueue entity rows."""

from __future__ import annotations

import uuid
from typing import TypeVar

import sqlalchemy as sa
from sqlalchemy.orm import Session

from agenticqueue_api.models import (
    ActorModel,
    ActorRecord,
    ArtifactModel,
    ArtifactRecord,
    AuditLogModel,
    AuditLogRecord,
    CapabilityModel,
    CapabilityRecord,
    DecisionModel,
    DecisionRecord,
    EdgeModel,
    EdgeRecord,
    EdgeRelation,
    LearningModel,
    LearningRecord,
    PacketVersionModel,
    PacketVersionRecord,
    PolicyModel,
    PolicyRecord,
    ProjectModel,
    ProjectRecord,
    RunModel,
    RunRecord,
    TaskModel,
    TaskRecord,
    WorkspaceModel,
    WorkspaceRecord,
)
from agenticqueue_api.models.shared import SchemaModel

SchemaT = TypeVar("SchemaT", bound=SchemaModel)
RecordT = TypeVar("RecordT")


def _create_entity(
    session: Session,
    record_type: type[RecordT],
    schema_type: type[SchemaT],
    payload: SchemaT,
) -> SchemaT:
    record = record_type(**payload.model_dump())  # type: ignore[call-arg]
    session.add(record)
    session.flush()
    session.refresh(record)
    return schema_type.model_validate(record)


def _get_entity(
    session: Session,
    record_type: type[RecordT],
    schema_type: type[SchemaT],
    entity_id: uuid.UUID,
) -> SchemaT | None:
    record = session.get(record_type, entity_id)
    if record is None:
        return None
    return schema_type.model_validate(record)


def create_workspace(session: Session, payload: WorkspaceModel) -> WorkspaceModel:
    return _create_entity(session, WorkspaceRecord, WorkspaceModel, payload)


def get_workspace(session: Session, entity_id: uuid.UUID) -> WorkspaceModel | None:
    return _get_entity(session, WorkspaceRecord, WorkspaceModel, entity_id)


def create_project(session: Session, payload: ProjectModel) -> ProjectModel:
    return _create_entity(session, ProjectRecord, ProjectModel, payload)


def get_project(session: Session, entity_id: uuid.UUID) -> ProjectModel | None:
    return _get_entity(session, ProjectRecord, ProjectModel, entity_id)


def create_task(session: Session, payload: TaskModel) -> TaskModel:
    return _create_entity(session, TaskRecord, TaskModel, payload)


def get_task(session: Session, entity_id: uuid.UUID) -> TaskModel | None:
    return _get_entity(session, TaskRecord, TaskModel, entity_id)


def create_run(session: Session, payload: RunModel) -> RunModel:
    return _create_entity(session, RunRecord, RunModel, payload)


def get_run(session: Session, entity_id: uuid.UUID) -> RunModel | None:
    return _get_entity(session, RunRecord, RunModel, entity_id)


def create_artifact(session: Session, payload: ArtifactModel) -> ArtifactModel:
    return _create_entity(session, ArtifactRecord, ArtifactModel, payload)


def get_artifact(session: Session, entity_id: uuid.UUID) -> ArtifactModel | None:
    return _get_entity(session, ArtifactRecord, ArtifactModel, entity_id)


def create_decision(session: Session, payload: DecisionModel) -> DecisionModel:
    return _create_entity(session, DecisionRecord, DecisionModel, payload)


def get_decision(session: Session, entity_id: uuid.UUID) -> DecisionModel | None:
    return _get_entity(session, DecisionRecord, DecisionModel, entity_id)


def create_edge(session: Session, payload: EdgeModel) -> EdgeModel:
    record = EdgeRecord(
        id=payload.id,
        created_at=payload.created_at,
        src_entity_type=payload.src_entity_type,
        src_id=payload.src_id,
        dst_entity_type=payload.dst_entity_type,
        dst_id=payload.dst_id,
        relation=payload.relation,
        edge_metadata=payload.metadata,
        created_by=payload.created_by,
    )
    session.add(record)
    session.flush()
    session.refresh(record)
    return EdgeModel.model_validate(record)


def get_edge(session: Session, entity_id: uuid.UUID) -> EdgeModel | None:
    return _get_entity(session, EdgeRecord, EdgeModel, entity_id)


def _list_edges(
    session: Session,
    *,
    entity_type_column: sa.ColumnElement[str],
    entity_id_column: sa.ColumnElement[uuid.UUID],
    entity_type: str,
    entity_id: uuid.UUID,
    relation: EdgeRelation | None,
    active_only: bool,
) -> list[EdgeModel]:
    statement = (
        sa.select(EdgeRecord)
        .where(entity_type_column == entity_type.strip(), entity_id_column == entity_id)
        .order_by(EdgeRecord.created_at.asc(), EdgeRecord.id.asc())
    )
    if relation is not None:
        statement = statement.where(EdgeRecord.relation == relation)

    edges = [EdgeModel.model_validate(record) for record in session.scalars(statement)]
    if not active_only:
        return edges
    return [edge for edge in edges if edge.is_active]


def list_edges_by_source(
    session: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    *,
    relation: EdgeRelation | None = None,
    active_only: bool = True,
) -> list[EdgeModel]:
    return _list_edges(
        session,
        entity_type_column=EdgeRecord.src_entity_type,
        entity_id_column=EdgeRecord.src_id,
        entity_type=entity_type,
        entity_id=entity_id,
        relation=relation,
        active_only=active_only,
    )


def list_edges_by_target(
    session: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    *,
    relation: EdgeRelation | None = None,
    active_only: bool = True,
) -> list[EdgeModel]:
    return _list_edges(
        session,
        entity_type_column=EdgeRecord.dst_entity_type,
        entity_id_column=EdgeRecord.dst_id,
        entity_type=entity_type,
        entity_id=entity_id,
        relation=relation,
        active_only=active_only,
    )


def create_actor(session: Session, payload: ActorModel) -> ActorModel:
    return _create_entity(session, ActorRecord, ActorModel, payload)


def get_actor(session: Session, entity_id: uuid.UUID) -> ActorModel | None:
    return _get_entity(session, ActorRecord, ActorModel, entity_id)


def create_capability(session: Session, payload: CapabilityModel) -> CapabilityModel:
    return _create_entity(session, CapabilityRecord, CapabilityModel, payload)


def get_capability(session: Session, entity_id: uuid.UUID) -> CapabilityModel | None:
    return _get_entity(session, CapabilityRecord, CapabilityModel, entity_id)


def create_policy(session: Session, payload: PolicyModel) -> PolicyModel:
    return _create_entity(session, PolicyRecord, PolicyModel, payload)


def get_policy(session: Session, entity_id: uuid.UUID) -> PolicyModel | None:
    return _get_entity(session, PolicyRecord, PolicyModel, entity_id)


def create_learning(session: Session, payload: LearningModel) -> LearningModel:
    return _create_entity(session, LearningRecord, LearningModel, payload)


def get_learning(session: Session, entity_id: uuid.UUID) -> LearningModel | None:
    return _get_entity(session, LearningRecord, LearningModel, entity_id)


def create_packet_version(
    session: Session,
    payload: PacketVersionModel,
) -> PacketVersionModel:
    return _create_entity(session, PacketVersionRecord, PacketVersionModel, payload)


def get_packet_version(
    session: Session,
    entity_id: uuid.UUID,
) -> PacketVersionModel | None:
    return _get_entity(session, PacketVersionRecord, PacketVersionModel, entity_id)


def create_audit_log(session: Session, payload: AuditLogModel) -> AuditLogModel:
    return _create_entity(session, AuditLogRecord, AuditLogModel, payload)


def get_audit_log(session: Session, entity_id: uuid.UUID) -> AuditLogModel | None:
    return _get_entity(session, AuditLogRecord, AuditLogModel, entity_id)
