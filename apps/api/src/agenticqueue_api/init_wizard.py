"""First-run setup helpers for migrations, seeding, and one-time admin token issuance."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Literal

import sqlalchemy as sa
from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy.orm import Session

from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.config import (
    get_alembic_config_path,
    get_direct_database_url,
    get_policies_dir,
)
from agenticqueue_api.models import (
    ActorRecord,
    PolicyRecord,
    ProjectRecord,
    TaskRecord,
    WorkspaceRecord,
)
from agenticqueue_api.models.shared import SchemaModel
from agenticqueue_api.policy.loader import PolicyLoadError, PolicyRegistry
from agenticqueue_api.seed import load_seed_fixture, seed_core_entities

DEFAULT_INIT_POLICY_NAME = "default-coding"
BOOT_TRACE_ID = "aq-init-boot"
CLI_TRACE_ID = "aq-init-cli"
SETUP_ROUTE_TRACE_ID = "aq-setup-route"


class InitWizardResult(SchemaModel):
    """Result payload for the first-run setup flow."""

    status: Literal["initialized", "noop"]
    message: str
    workspace_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    actor_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    policy_id: uuid.UUID | None = None
    policy_name: str = DEFAULT_INIT_POLICY_NAME
    api_token: str | None = None


def apply_database_migrations(config_path: Path | None = None) -> None:
    """Bring the configured database to Alembic head."""

    config = Config(str(config_path or get_alembic_config_path()))
    original_database_url = os.environ.get("AGENTICQUEUE_DATABASE_URL")
    os.environ["AGENTICQUEUE_DATABASE_URL"] = get_direct_database_url()
    try:
        upgrade(config, "head")
    finally:
        if original_database_url is None:
            os.environ.pop("AGENTICQUEUE_DATABASE_URL", None)
        else:
            os.environ["AGENTICQUEUE_DATABASE_URL"] = original_database_url


def _existing_bootstrap_state(session: Session) -> InitWizardResult | None:
    workspace = session.scalar(
        sa.select(WorkspaceRecord).order_by(
            WorkspaceRecord.created_at.asc(),
            WorkspaceRecord.id.asc(),
        )
    )
    if workspace is None:
        return None

    project_id = session.scalar(
        sa.select(ProjectRecord.id)
        .where(ProjectRecord.workspace_id == workspace.id)
        .order_by(ProjectRecord.created_at.asc(), ProjectRecord.id.asc())
    )
    actor_id = session.scalar(
        sa.select(ActorRecord.id).order_by(
            ActorRecord.created_at.asc(),
            ActorRecord.id.asc(),
        )
    )
    task_id = None
    if project_id is not None:
        task_id = session.scalar(
            sa.select(TaskRecord.id)
            .where(TaskRecord.project_id == project_id)
            .order_by(TaskRecord.created_at.asc(), TaskRecord.id.asc())
        )

    return InitWizardResult(
        status="noop",
        message="Existing workspace detected; first-run init skipped.",
        workspace_id=workspace.id,
        project_id=project_id,
        actor_id=actor_id,
        task_id=task_id,
        policy_id=workspace.policy_id,
    )


def _ensure_policy_pack(session: Session, *, policy_name: str) -> PolicyRecord:
    registry = PolicyRegistry(get_policies_dir())
    try:
        registry.load()
        pack = registry.get(policy_name)
    except PolicyLoadError as error:
        raise RuntimeError(str(error)) from error

    existing = session.scalar(
        sa.select(PolicyRecord)
        .where(
            PolicyRecord.workspace_id.is_(None),
            PolicyRecord.name == pack.name,
            PolicyRecord.version == pack.version,
        )
        .limit(1)
    )
    if existing is not None:
        return existing

    record = PolicyRecord(
        workspace_id=None,
        name=pack.name,
        version=pack.version,
        hitl_required=pack.hitl_required,
        autonomy_tier=pack.autonomy_tier,
        capabilities=list(pack.capabilities),
        body=pack.body,
    )
    session.add(record)
    session.flush()
    session.refresh(record)
    return record


def run_first_run_setup(
    session: Session,
    *,
    policy_name: str = DEFAULT_INIT_POLICY_NAME,
) -> InitWizardResult:
    """Seed the default local workspace and emit a one-time admin token."""

    existing = _existing_bootstrap_state(session)
    if existing is not None:
        return existing

    fixture = load_seed_fixture()
    seeded = seed_core_entities(session, fixture)
    policy = _ensure_policy_pack(session, policy_name=policy_name)

    workspace = session.get(WorkspaceRecord, seeded.workspace_id)
    project = session.get(ProjectRecord, seeded.project_id)
    task = session.get(TaskRecord, seeded.task_id)
    if workspace is None or project is None or task is None:
        raise RuntimeError("first-run seed failed to create the expected entity set")

    workspace.policy_id = policy.id
    project.policy_id = policy.id
    task.policy_id = policy.id

    _, raw_token = issue_api_token(
        session,
        actor_id=seeded.actor_id,
        scopes=["admin"],
        expires_at=None,
    )

    session.flush()

    return InitWizardResult(
        status="initialized",
        message="AgenticQueue first-run init completed. Save the one-time admin token now.",
        workspace_id=seeded.workspace_id,
        project_id=seeded.project_id,
        actor_id=seeded.actor_id,
        task_id=seeded.task_id,
        policy_id=policy.id,
        policy_name=policy.name,
        api_token=raw_token,
    )


def emit_bootstrap_message(result: InitWizardResult) -> None:
    """Print the startup bootstrap status for docker-compose users."""

    print(f"[aq-init] {result.message}")
    if result.api_token is not None:
        print(f"[aq-init] One-time admin token: {result.api_token}")
