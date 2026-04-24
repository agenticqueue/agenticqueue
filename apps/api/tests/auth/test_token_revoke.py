from __future__ import annotations

import sqlalchemy as sa

from agenticqueue_api.models import CapabilityKey
from conftest import seed_actor, seed_capability, seed_token


def test_delete_token_rejects_missing_bearer(client) -> None:
    response = client.delete("/v1/auth/tokens/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 401
    assert response.json()["error_code"] == "auth_failed"


def test_delete_token_revoke_is_idempotent_and_blocks_future_use(
    client,
    session_factory,
) -> None:
    actor = seed_actor(
        session_factory,
        handle="token-owner",
        actor_type="agent",
        display_name="Token Owner",
    )
    seed_capability(
        session_factory,
        actor_id=actor.id,
        capability=CapabilityKey.ADMIN,
    )
    raw_token, token_id = seed_token(
        session_factory,
        actor_id=actor.id,
        scopes=["read:tokens"],
    )

    first = client.delete(
        f"/v1/auth/tokens/{token_id}",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    second = client.delete(
        f"/v1/auth/tokens/{token_id}",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    after = client.get(
        "/v1/auth/tokens",
        headers={"Authorization": f"Bearer {raw_token}"},
    )

    assert first.status_code == 204
    assert second.status_code == 204
    assert after.status_code == 401
    with session_factory() as session:
        audit_actor_id = session.scalar(sa.text("""
                SELECT actor_id
                FROM agenticqueue.auth_audit_log
                WHERE action = 'TOKEN_REVOKED'
                ORDER BY created_at DESC
                LIMIT 1
                """))
    assert str(audit_actor_id) == str(actor.id)
