from __future__ import annotations

import asyncio
import uuid

import sqlalchemy as sa
from fastmcp import Client as FastMCPClient

from agenticqueue_api.auth.hashing import hash_passcode
from agenticqueue_api.models import CapabilityKey
from conftest import (
    coding_task_payload,
    seed_actor,
    seed_capability,
    seed_project,
    seed_token,
    seed_workspace,
)


def _mcp_call(server, tool_name: str, arguments: dict[str, object]):
    async def _invoke():
        async with FastMCPClient(server) as client:
            result = await client.call_tool(tool_name, arguments)
            return result.data

    return asyncio.run(_invoke())


def test_projects_mine_returns_only_user_memberships(client, session_factory) -> None:
    workspace_id = seed_workspace(
        session_factory, slug="membership-ws", name="Membership"
    )
    alice_project = seed_project(
        session_factory,
        workspace_id=workspace_id,
        slug="alice",
        name="Alice Project",
    )
    shared_project = seed_project(
        session_factory,
        workspace_id=workspace_id,
        slug="shared",
        name="Shared Project",
    )
    bob_project = seed_project(
        session_factory,
        workspace_id=workspace_id,
        slug="bob",
        name="Bob Project",
    )

    with session_factory() as session:
        alice_id = uuid.uuid4()
        session.execute(
            sa.text("""
                INSERT INTO agenticqueue.users
                  (id, username, passcode_hash, is_admin, is_active)
                VALUES (:id, 'alice', :passcode_hash, false, true)
                """),
            {"id": alice_id, "passcode_hash": hash_passcode("alice-passcode")},
        )
        session.execute(
            sa.text("""
                INSERT INTO agenticqueue.project_members (user_id, project_id, role)
                VALUES (:user_id, :alice_project, 'owner'),
                       (:user_id, :shared_project, 'member')
                """),
            {
                "user_id": alice_id,
                "alice_project": alice_project,
                "shared_project": shared_project,
            },
        )
        session.commit()

    login = client.post(
        "/v1/auth/login",
        json={"username": "alice", "passcode": "alice-passcode"},
    )
    assert login.status_code == 200

    response = client.get("/v1/projects/mine")

    assert response.status_code == 200
    assert {project["id"] for project in response.json()["projects"]} == {
        str(alice_project),
        str(shared_project),
    }
    assert str(bob_project) not in {
        project["id"] for project in response.json()["projects"]
    }


def test_agent_token_write_to_cross_project_returns_403_via_mcp(
    client,
    session_factory,
) -> None:
    del client
    workspace_id = seed_workspace(
        session_factory, slug="mcp-scope-ws", name="MCP Scope"
    )
    shared_project = seed_project(
        session_factory,
        workspace_id=workspace_id,
        slug="shared-scope",
        name="Shared Scope",
    )
    bob_project = seed_project(
        session_factory,
        workspace_id=workspace_id,
        slug="bob-scope",
        name="Bob Scope",
    )
    actor = seed_actor(
        session_factory,
        handle="scoped-agent",
        actor_type="agent",
        display_name="Scoped Agent",
    )
    seed_capability(
        session_factory,
        actor_id=actor.id,
        capability=CapabilityKey.WRITE_BRANCH,
        project_id=shared_project,
    )
    token, _ = seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=["task:write"],
    )

    from agenticqueue_api.app import create_app

    app = create_app(session_factory=session_factory)
    response = _mcp_call(
        app.state.mcp_server,
        "create_job",
        {
            "token": token,
            "payload": coding_task_payload(project_id=bob_project, title="Bob-only"),
        },
    )

    assert response["error_code"] == "forbidden", response
    assert response["details"]["required_scope"] == {"project_id": str(bob_project)}
