"""Local human auth and project membership routes."""

from __future__ import annotations

import uuid
import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import Field
from sqlalchemy.orm import Session

from agenticqueue_api.auth import (
    AuthenticationError,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    extract_bearer_token,
    resolve_api_token,
)
from agenticqueue_api.auth.local import (
    authenticate_user_passcode,
    create_browser_session,
    list_user_projects,
    revoke_agent_token_audit,
    revoke_browser_session,
)
from agenticqueue_api.auth.reset import (
    LastAdminResetRequiresForceError,
    UnknownUserError,
    reset_user_passcode,
)
from agenticqueue_api.errors import raise_api_error
from agenticqueue_api.middleware.csrf import CSRF_COOKIE_NAME
from agenticqueue_api.models import (
    ApiTokenRecord,
    ProjectMemberRecord,
    ProjectRecord,
    UserModel,
)
from agenticqueue_api.models.shared import SchemaModel


class LoginRequest(SchemaModel):
    """Local passcode login payload."""

    username: str = Field(min_length=1, max_length=120)
    passcode: str = Field(min_length=1)


class LoginResponse(SchemaModel):
    """Successful local login response."""

    user: dict[str, Any]


class ResetPasscodeRequest(SchemaModel):
    """Admin passcode reset payload."""

    username: str = Field(min_length=1, max_length=120)
    force: bool = False


class ResetPasscodeResponse(SchemaModel):
    """One-time passcode reset response."""

    username: str
    user_id: uuid.UUID
    passcode: str
    sessions_deleted: int


class ProjectMemberRequest(SchemaModel):
    """Project membership creation payload."""

    user_id: uuid.UUID
    role: str = Field(min_length=1, max_length=64)


class ProjectMemberResponse(SchemaModel):
    """Project membership response."""

    user_id: uuid.UUID
    project_id: uuid.UUID
    role: str


class MyProjectsResponse(SchemaModel):
    """Projects visible to the authenticated human user."""

    projects: list[dict[str, Any]]


def _client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    if request.client is None:
        return None
    return request.client.host


def _require_user(request: Request) -> UserModel:
    user = getattr(request.state, "user", None)
    if user is None:
        raise_api_error(status.HTTP_401_UNAUTHORIZED, "Local user session required")
    return user


def _require_admin_user(request: Request) -> UserModel:
    user = _require_user(request)
    if not user.is_admin:
        raise_api_error(status.HTTP_403_FORBIDDEN, "Admin user required")
    return user


def _set_session_cookies(
    response: Response,
    *,
    session_token: str,
    csrf_token: str,
) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=SESSION_MAX_AGE_SECONDS,
        path="/",
        secure=True,
        httponly=True,
        samesite="Lax",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        max_age=SESSION_MAX_AGE_SECONDS,
        path="/",
        secure=True,
        httponly=False,
        samesite="Lax",
    )


def build_auth_router(get_db_session: Any) -> APIRouter:
    """Build local auth routes."""

    router = APIRouter(prefix="/v1")

    @router.post("/auth/login", response_model=LoginResponse)
    def login(
        payload: LoginRequest,
        request: Request,
        response: Response,
        session: Session = Depends(get_db_session),
    ) -> LoginResponse:
        user = authenticate_user_passcode(
            session,
            username=payload.username,
            passcode=payload.passcode,
        )
        if user is None:
            raise_api_error(
                status.HTTP_401_UNAUTHORIZED,
                "Invalid username or passcode",
                error_code="auth_failed",
            )

        session_token, csrf_token, _ = create_browser_session(
            session,
            user=user,
            ip_address=_client_ip(request),
        )
        _set_session_cookies(
            response,
            session_token=session_token,
            csrf_token=csrf_token,
        )
        return LoginResponse(
            user={
                "id": str(user.id),
                "username": user.username,
                "is_admin": user.is_admin,
            }
        )

    @router.post("/auth/logout")
    def logout(
        request: Request,
        response: Response,
        session: Session = Depends(get_db_session),
    ) -> dict[str, bool]:
        user = _require_user(request)
        auth_session_id = getattr(request.state, "auth_session_id", None)
        if not isinstance(auth_session_id, uuid.UUID):
            raise_api_error(status.HTTP_401_UNAUTHORIZED, "Local user session required")
        revoke_browser_session(
            session,
            session_id=auth_session_id,
            user_id=user.id,
            ip_address=_client_ip(request),
        )
        response.delete_cookie(SESSION_COOKIE_NAME, path="/")
        response.delete_cookie(CSRF_COOKIE_NAME, path="/")
        return {"ok": True}

    @router.post("/auth/reset-passcode", response_model=ResetPasscodeResponse)
    def reset_passcode(
        payload: ResetPasscodeRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> ResetPasscodeResponse:
        admin_user = _require_admin_user(request)
        if admin_user.actor_id is None:
            raise_api_error(
                status.HTTP_403_FORBIDDEN,
                "Admin actor link required",
            )
        try:
            result = reset_user_passcode(
                session,
                username=payload.username,
                actor_id=admin_user.actor_id,
                method="api",
                force=payload.force,
            )
        except UnknownUserError as error:
            raise_api_error(status.HTTP_404_NOT_FOUND, str(error))
        except LastAdminResetRequiresForceError as error:
            raise_api_error(status.HTTP_409_CONFLICT, str(error))
        return ResetPasscodeResponse(**result.to_public_dict())

    @router.get("/projects/mine", response_model=MyProjectsResponse)
    def list_my_projects(
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> MyProjectsResponse:
        user = _require_user(request)
        return MyProjectsResponse(projects=list_user_projects(session, user.id))

    @router.post(
        "/projects/{project_id}/members",
        response_model=ProjectMemberResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def add_project_member(
        project_id: uuid.UUID,
        payload: ProjectMemberRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> ProjectMemberResponse:
        _require_admin_user(request)
        if session.get(ProjectRecord, project_id) is None:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Project not found")
        record = ProjectMemberRecord(
            user_id=payload.user_id,
            project_id=project_id,
            role=payload.role,
        )
        session.add(record)
        session.flush()
        session.refresh(record)
        return ProjectMemberResponse(
            user_id=record.user_id,
            project_id=record.project_id,
            role=record.role,
        )

    @router.delete("/auth/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_token(
        token_id: uuid.UUID,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> Response:
        try:
            bearer_token = extract_bearer_token(request.headers.get("Authorization"))
        except AuthenticationError:
            raise_api_error(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")
        authenticated = resolve_api_token(
            session,
            bearer_token,
            include_revoked=True,
        )
        if authenticated is None:
            raise_api_error(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")
        token_record = session.get(ApiTokenRecord, token_id)
        if token_record is None or token_record.actor_id != authenticated.actor.id:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Token not found")
        if token_record.revoked_at is None:
            token_record.revoked_at = dt.datetime.now(dt.UTC)
            revoke_agent_token_audit(
                session,
                actor_id=authenticated.actor.id,
                token_id=token_id,
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
