"""Dedicated RBAC routes kept separate from app composition wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import uuid

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from agenticqueue_api.capabilities import (
    grant_capability,
    list_capabilities_for_actor,
    revoke_capability_grant,
)
from agenticqueue_api.db import write_timeout
from agenticqueue_api.errors import raise_api_error
from agenticqueue_api.models import ActorRecord
from agenticqueue_api.pagination import DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT
from agenticqueue_api.roles import (
    assign_role,
    list_role_assignments_for_actor,
    list_roles,
    revoke_role_assignment,
)

if TYPE_CHECKING:
    from agenticqueue_api.app import (
        ActorCapabilityListResponse,
        ActorRoleListResponse,
        AssignRoleRequest,
        CapabilityGrantView,
        GrantCapabilityRequest,
        RevokeCapabilityRequest,
        RevokeRoleRequest,
        RoleAssignmentView,
        RoleListResponse,
    )


def build_rbac_router(get_db_session: Any) -> APIRouter:
    """Build the dedicated RBAC router."""

    from agenticqueue_api import app as app_module

    globals()["ActorCapabilityListResponse"] = app_module.ActorCapabilityListResponse
    globals()["ActorRoleListResponse"] = app_module.ActorRoleListResponse
    globals()["AssignRoleRequest"] = app_module.AssignRoleRequest
    globals()["CapabilityGrantView"] = app_module.CapabilityGrantView
    globals()["GrantCapabilityRequest"] = app_module.GrantCapabilityRequest
    globals()["RevokeCapabilityRequest"] = app_module.RevokeCapabilityRequest
    globals()["RevokeRoleRequest"] = app_module.RevokeRoleRequest
    globals()["RoleAssignmentView"] = app_module.RoleAssignmentView
    globals()["RoleListResponse"] = app_module.RoleListResponse

    router = APIRouter()

    @router.post(
        "/v1/capabilities/grant",
        response_model=CapabilityGrantView,
        status_code=status.HTTP_201_CREATED,
    )
    def grant_capability_endpoint(
        payload: GrantCapabilityRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> CapabilityGrantView:
        with write_timeout(session, endpoint="v1.capabilities.grant"):
            admin_actor = app_module._require_admin_actor(request)
            actor_exists = session.get(ActorRecord, payload.actor_id)
            if actor_exists is None:
                raise_api_error(status.HTTP_404_NOT_FOUND, "Actor not found")

            try:
                grant = grant_capability(
                    session,
                    actor_id=payload.actor_id,
                    capability=payload.capability,
                    scope=payload.scope,
                    granted_by_actor_id=admin_actor.id,
                    expires_at=payload.expires_at,
                )
            except ValueError as error:
                raise_api_error(status.HTTP_404_NOT_FOUND, str(error))
            return app_module._capability_grant_view(grant)

    @router.post("/v1/capabilities/revoke", response_model=CapabilityGrantView)
    def revoke_capability_endpoint(
        payload: RevokeCapabilityRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> CapabilityGrantView:
        with write_timeout(session, endpoint="v1.capabilities.revoke"):
            app_module._require_admin_actor(request)
            revoked_grant = revoke_capability_grant(session, payload.grant_id)
            if revoked_grant is None:
                raise_api_error(
                    status.HTTP_404_NOT_FOUND,
                    "Capability grant not found",
                )
            return app_module._capability_grant_view(revoked_grant)

    @router.get(
        "/v1/actors/{actor_id}/capabilities",
        response_model=ActorCapabilityListResponse,
    )
    def list_capability_grants(
        actor_id: uuid.UUID,
        request: Request,
        response: Response,
        limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
        cursor: str | None = Query(default=None),
        session: Session = Depends(get_db_session),
    ) -> ActorCapabilityListResponse:
        requesting_actor = app_module._require_actor(request)
        if requesting_actor.actor_type != "admin" and requesting_actor.id != actor_id:
            raise_api_error(status.HTTP_403_FORBIDDEN, "Admin actor required")

        target_actor = session.get(ActorRecord, actor_id)
        if target_actor is None:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Actor not found")

        grants = list_capabilities_for_actor(session, actor_id)
        page = app_module._paginate_sequence(
            grants,
            response=response,
            limit=limit,
            cursor=cursor,
            key_types=[str, str],
            key_fn=lambda grant: [grant.created_at.isoformat(), str(grant.id)],
        )
        return ActorCapabilityListResponse(
            actor=app_module._actor_summary(
                app_module.ActorModel.model_validate(target_actor)
            ),
            capabilities=[app_module._capability_grant_view(grant) for grant in page],
        )

    @router.get("/v1/roles", response_model=RoleListResponse)
    def list_roles_endpoint(
        request: Request,
        response: Response,
        limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
        cursor: str | None = Query(default=None),
        session: Session = Depends(get_db_session),
    ) -> RoleListResponse:
        app_module._require_admin_actor(request)
        roles = sorted(
            list_roles(session),
            key=lambda role: (role.created_at.isoformat(), str(role.id)),
        )
        page = app_module._paginate_sequence(
            roles,
            response=response,
            limit=limit,
            cursor=cursor,
            key_types=[str, str],
            key_fn=lambda role: [role.created_at.isoformat(), str(role.id)],
        )
        return RoleListResponse(roles=[app_module._role_view(role) for role in page])

    @router.post(
        "/v1/roles/assign",
        response_model=RoleAssignmentView,
        status_code=status.HTTP_201_CREATED,
    )
    def assign_role_endpoint(
        payload: AssignRoleRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> RoleAssignmentView:
        with write_timeout(session, endpoint="v1.roles.assign"):
            admin_actor = app_module._require_admin_actor(request)
            actor_exists = session.get(ActorRecord, payload.actor_id)
            if actor_exists is None:
                raise_api_error(status.HTTP_404_NOT_FOUND, "Actor not found")

            try:
                assignment = assign_role(
                    session,
                    actor_id=payload.actor_id,
                    role_name=payload.role_name,
                    granted_by_actor_id=admin_actor.id,
                    expires_at=payload.expires_at,
                )
            except ValueError as error:
                raise_api_error(status.HTTP_404_NOT_FOUND, str(error))
            return app_module._role_assignment_view(assignment)

    @router.post("/v1/roles/revoke", response_model=RoleAssignmentView)
    def revoke_role_endpoint(
        payload: RevokeRoleRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> RoleAssignmentView:
        with write_timeout(session, endpoint="v1.roles.revoke"):
            app_module._require_admin_actor(request)
            assignment = revoke_role_assignment(session, payload.assignment_id)
            if assignment is None:
                raise_api_error(status.HTTP_404_NOT_FOUND, "Role assignment not found")
            return app_module._role_assignment_view(assignment)

    @router.get("/v1/actors/{actor_id}/roles", response_model=ActorRoleListResponse)
    def list_actor_roles(
        actor_id: uuid.UUID,
        request: Request,
        response: Response,
        limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
        cursor: str | None = Query(default=None),
        session: Session = Depends(get_db_session),
    ) -> ActorRoleListResponse:
        requesting_actor = app_module._require_actor(request)
        if requesting_actor.actor_type != "admin" and requesting_actor.id != actor_id:
            raise_api_error(status.HTTP_403_FORBIDDEN, "Admin actor required")

        target_actor = session.get(ActorRecord, actor_id)
        if target_actor is None:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Actor not found")

        assignments = list_role_assignments_for_actor(session, actor_id)
        page = app_module._paginate_sequence(
            assignments,
            response=response,
            limit=limit,
            cursor=cursor,
            key_types=[str, str],
            key_fn=lambda assignment: [
                assignment.created_at.isoformat(),
                str(assignment.id),
            ],
        )
        return ActorRoleListResponse(
            actor=app_module._actor_summary(
                app_module.ActorModel.model_validate(target_actor)
            ),
            roles=[app_module._role_assignment_view(assignment) for assignment in page],
        )

    return router


__all__ = ["build_rbac_router"]
