"""Deterministic seed data helpers for local AgenticQueue development."""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from typing import Any, TypeVar, cast

import sqlalchemy as sa
import yaml  # type: ignore[import-untyped]
from pydantic import Field
from sqlalchemy.orm import Session

from agenticqueue_api.auth import _hash_token_secret, token_display_prefix
from agenticqueue_api.models import (
    ActorModel,
    ActorRecord,
    ApiTokenModel,
    ApiTokenRecord,
    ProjectModel,
    ProjectRecord,
    TaskModel,
    TaskRecord,
    WorkspaceModel,
    WorkspaceRecord,
)
from agenticqueue_api.models.shared import SchemaModel, TimestampedSchema

DEFAULT_SEED_PATH = Path("examples") / "seed.yaml"

SchemaT = TypeVar("SchemaT", bound=TimestampedSchema)
RecordT = TypeVar("RecordT")


class SeedWorkspace(TimestampedSchema):
    """Workspace fixture loaded from examples/seed.yaml."""

    slug: str
    name: str
    description: str | None = None

    def to_model(self) -> WorkspaceModel:
        return WorkspaceModel.model_validate(self.model_dump())


class SeedProject(TimestampedSchema):
    """Project fixture loaded from examples/seed.yaml."""

    slug: str
    name: str
    description: str | None = None

    def to_model(self, *, workspace_id: uuid.UUID) -> ProjectModel:
        return ProjectModel.model_validate(
            {
                **self.model_dump(),
                "workspace_id": str(workspace_id),
            }
        )


class SeedActor(TimestampedSchema):
    """Actor fixture loaded from examples/seed.yaml."""

    handle: str
    actor_type: str
    display_name: str
    auth_subject: str | None = None
    is_active: bool

    def to_model(self) -> ActorModel:
        return ActorModel.model_validate(self.model_dump())


class SeedToken(TimestampedSchema):
    """API token fixture loaded from examples/seed.yaml."""

    raw_secret: str
    scopes: list[str] = Field(default_factory=list)
    expires_at: dt.datetime | None = None
    revoked_at: dt.datetime | None = None

    def _token_hash(self) -> str:
        return _hash_token_secret(self.raw_secret)

    def render_raw_token(self) -> str:
        return f"{token_display_prefix(self._token_hash())}_{self.raw_secret}"

    def to_model(self, *, actor_id: uuid.UUID) -> ApiTokenModel:
        return ApiTokenModel.model_validate(
            {
                **self.model_dump(exclude={"raw_secret"}),
                "actor_id": str(actor_id),
                "token_hash": self._token_hash(),
            }
        )


class SeedTask(TimestampedSchema):
    """Task fixture loaded from examples/seed.yaml."""

    task_type: str
    title: str
    state: str
    description: str | None = None
    contract: dict[str, object] = Field(default_factory=dict)
    definition_of_done: list[str] = Field(default_factory=list)

    def to_model(self, *, project_id: uuid.UUID) -> TaskModel:
        return TaskModel.model_validate(
            {
                **self.model_dump(),
                "project_id": str(project_id),
            }
        )


class SeedFixture(SchemaModel):
    """Complete deterministic seed payload."""

    workspace: SeedWorkspace
    project: SeedProject
    actor: SeedActor
    token: SeedToken
    task: SeedTask


class SeedResult(SchemaModel):
    """Stable CLI output for aq seed."""

    workspace_id: uuid.UUID
    project_id: uuid.UUID
    actor_id: uuid.UUID
    task_id: uuid.UUID
    api_token: str


class SeedState(SchemaModel):
    """Stable identifiers for seeded core entities without token issuance."""

    workspace_id: uuid.UUID
    project_id: uuid.UUID
    actor_id: uuid.UUID
    task_id: uuid.UUID


def _persistable_payload(
    record_type: type[RecordT],
    payload: SchemaT,
) -> dict[str, object]:
    """Drop schema-only fields before touching SQLAlchemy records."""

    mapper = sa.inspect(cast(Any, record_type))
    assert mapper is not None
    column_names = set(mapper.columns.keys())
    return {
        field_name: value
        for field_name, value in payload.model_dump(exclude_none=True).items()
        if field_name in column_names
    }


def load_seed_fixture(seed_path: Path = DEFAULT_SEED_PATH) -> SeedFixture:
    """Load the canonical deterministic seed fixture."""

    with seed_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return SeedFixture.model_validate(payload)


def _upsert_entity(
    session: Session,
    *,
    record_type: type[RecordT],
    schema_type: type[SchemaT],
    payload: SchemaT,
) -> SchemaT:
    record = session.get(record_type, payload.id)
    if record is None:
        record = record_type(**_persistable_payload(record_type, payload))  # type: ignore[call-arg]
        session.add(record)
    else:
        for field_name, value in _persistable_payload(record_type, payload).items():
            if field_name in {"id", "created_at", "updated_at"}:
                continue
            setattr(record, field_name, value)

    session.flush()
    session.refresh(record)
    return schema_type.model_validate(record)


def seed_core_entities(session: Session, fixture: SeedFixture) -> SeedState:
    """Create or update the deterministic workspace/project/actor/task slice."""

    workspace = _upsert_entity(
        session,
        record_type=WorkspaceRecord,
        schema_type=WorkspaceModel,
        payload=fixture.workspace.to_model(),
    )
    project = _upsert_entity(
        session,
        record_type=ProjectRecord,
        schema_type=ProjectModel,
        payload=fixture.project.to_model(workspace_id=workspace.id),
    )
    actor = _upsert_entity(
        session,
        record_type=ActorRecord,
        schema_type=ActorModel,
        payload=fixture.actor.to_model(),
    )
    task = _upsert_entity(
        session,
        record_type=TaskRecord,
        schema_type=TaskModel,
        payload=fixture.task.to_model(project_id=project.id),
    )

    return SeedState(
        workspace_id=workspace.id,
        project_id=project.id,
        actor_id=actor.id,
        task_id=task.id,
    )


def seed_example_data(session: Session, fixture: SeedFixture) -> SeedResult:
    """Create or update the deterministic local example dataset."""

    state = seed_core_entities(session, fixture)
    _upsert_entity(
        session,
        record_type=ApiTokenRecord,
        schema_type=ApiTokenModel,
        payload=fixture.token.to_model(actor_id=state.actor_id),
    )

    return SeedResult(
        workspace_id=state.workspace_id,
        project_id=state.project_id,
        actor_id=state.actor_id,
        task_id=state.task_id,
        api_token=fixture.token.render_raw_token(),
    )
