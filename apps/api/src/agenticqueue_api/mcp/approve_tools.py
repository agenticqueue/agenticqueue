"""AgenticQueue MCP approval and queue-recovery tools."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from fastmcp import FastMCP
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.models import AuditLogRecord, TaskModel, TaskRecord
from agenticqueue_api.dod import DodChecklistResult, DodReport
from agenticqueue_api.dod_checks.common import DodItemState
from agenticqueue_api.mcp.common import run_session_tool, surface_error
from agenticqueue_api.repo import release_claim
from agenticqueue_api.task_type_registry import TaskTypeRegistry
from agenticqueue_api.transitions import TaskState, apply_transition


def _passing_dod_report(task: TaskModel) -> DodReport:
    checklist = tuple(
        DodChecklistResult(
            item=item,
            state=DodItemState.CHECKED,
            note="Approved via MCP approval tool.",
        )
        for item in task.definition_of_done
    )
    checked_count = len(checklist)
    return DodReport(
        checklist=checklist,
        checked_count=checked_count,
        partial_count=0,
        unchecked_blocked_count=0,
        unchecked_unmet_count=0,
    )


def _audit(
    session: Session,
    *,
    actor_id: uuid.UUID | None,
    task_id: uuid.UUID,
    action: str,
    after: dict[str, Any],
) -> None:
    session.execute(
        sa.insert(AuditLogRecord).values(
            actor_id=actor_id,
            entity_type="task",
            entity_id=task_id,
            action=action,
            before=None,
            after=after,
            trace_id=session.info.get("agenticqueue_audit_trace_id"),
            redaction=session.info.get("agenticqueue_audit_redaction"),
        )
    )


def _task_or_error(session: Session, job_id: uuid.UUID) -> TaskRecord:
    task = session.get(TaskRecord, job_id)
    if task is None:
        raise surface_error(404, "Job not found")
    return task


def register_approve_tools(
    mcp: FastMCP,
    *,
    session_factory: sessionmaker[Session],
    task_type_registry: TaskTypeRegistry,
) -> set[str]:
    """Register approval and queue-recovery tools."""

    registered: set[str] = set()

    @mcp.tool(
        name="approve_job",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def approve_job(
        job_id: uuid.UUID,
        token: str | None = None,
    ) -> dict[str, Any]:
        def _callback(session: Session, authenticated) -> dict[str, Any]:
            task = _task_or_error(session, job_id)
            task_model = TaskModel.model_validate(task)
            result = apply_transition(
                task_model,
                TaskState.DONE,
                task_type_registry,
                human_approved=True,
                dod_report=_passing_dod_report(task_model),
                actor_capabilities=["update_task"],
            )
            if result.guard_blocked is not None:
                raise surface_error(
                    409,
                    result.note or "Job cannot be approved in its current state",
                    details=result.__dict__,
                )
            task.state = result.state
            task.claimed_by_actor_id = None
            task.claimed_at = None
            _audit(
                session,
                actor_id=authenticated.actor.id,
                task_id=task.id,
                action="JOB_APPROVED",
                after={"state": task.state},
            )
            session.flush()
            session.refresh(task)
            return TaskModel.model_validate(task).model_dump(mode="json")

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="approve-job",
            callback=_callback,
            mutation=True,
        )

    registered.add("approve_job")

    @mcp.tool(
        name="reject_job",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def reject_job(
        job_id: uuid.UUID,
        token: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        def _callback(session: Session, authenticated) -> dict[str, Any]:
            task = _task_or_error(session, job_id)
            if task.state not in {TaskState.SUBMITTED.value, TaskState.VALIDATED.value}:
                raise surface_error(
                    409,
                    "Only submitted or validated jobs can be rejected",
                    details={"state": task.state},
                )
            task.state = TaskState.REJECTED.value
            task.claimed_by_actor_id = None
            task.claimed_at = None
            _audit(
                session,
                actor_id=authenticated.actor.id,
                task_id=task.id,
                action="JOB_REJECTED",
                after={"state": task.state, "reason": reason},
            )
            session.flush()
            session.refresh(task)
            return TaskModel.model_validate(task).model_dump(mode="json")

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="reject-job",
            callback=_callback,
            mutation=True,
        )

    registered.add("reject_job")

    @mcp.tool(
        name="force_unlock_escrow",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def force_unlock_escrow(
        job_id: uuid.UUID,
        token: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        def _callback(session: Session, authenticated) -> dict[str, Any]:
            if authenticated.actor.actor_type != "admin":
                raise surface_error(403, "Admin actor required")
            released = release_claim(session, task_id=job_id, expected_actor_id=None)
            if released is None:
                raise surface_error(404, "Job not found or not unlockable")
            _audit(
                session,
                actor_id=authenticated.actor.id,
                task_id=released.id,
                action="ESCROW_FORCE_UNLOCKED",
                after={"state": released.state, "reason": reason},
            )
            return released.model_dump(mode="json")

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="force-unlock-escrow",
            callback=_callback,
            mutation=True,
        )

    registered.add("force_unlock_escrow")

    @mcp.tool(
        name="reset_job",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def reset_job(
        job_id: uuid.UUID,
        token: str | None = None,
    ) -> dict[str, Any]:
        def _callback(session: Session, authenticated) -> dict[str, Any]:
            if authenticated.actor.actor_type != "admin":
                raise surface_error(403, "Admin actor required")
            task = _task_or_error(session, job_id)
            task.state = TaskState.QUEUED.value
            task.claimed_by_actor_id = None
            task.claimed_at = None
            _audit(
                session,
                actor_id=authenticated.actor.id,
                task_id=task.id,
                action="JOB_RESET",
                after={"state": task.state},
            )
            session.flush()
            session.refresh(task)
            return TaskModel.model_validate(task).model_dump(mode="json")

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="reset-job",
            callback=_callback,
            mutation=True,
        )

    registered.add("reset_job")

    @mcp.tool(
        name="comment_on_job",
        annotations={"readOnlyHint": False, "openWorldHint": False},
    )
    def comment_on_job(
        job_id: uuid.UUID,
        body: str,
        token: str | None = None,
    ) -> dict[str, Any]:
        def _callback(session: Session, authenticated) -> dict[str, Any]:
            task = _task_or_error(session, job_id)
            _audit(
                session,
                actor_id=authenticated.actor.id,
                task_id=task.id,
                action="JOB_COMMENTED",
                after={"body": body, "commented_at": dt.datetime.now(dt.UTC).isoformat()},
            )
            return {"job_id": str(task.id), "commented": True}

        return run_session_tool(
            session_factory,
            token=token,
            trace_name="comment-on-job",
            callback=_callback,
            mutation=True,
        )

    registered.add("comment_on_job")

    return registered
