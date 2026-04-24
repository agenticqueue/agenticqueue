from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from typer.testing import CliRunner

from agenticqueue_api.auth.hashing import hash_passcode, verify_passcode
from agenticqueue_api.cli import app as cli_app
from agenticqueue_api.models import ActorRecord, UserRecord


def _seed_user(
    session_factory,
    *,
    username: str,
    passcode: str,
    is_admin: bool = False,
) -> tuple[uuid.UUID, uuid.UUID]:
    with session_factory() as session:
        actor = ActorRecord(
            handle=username,
            actor_type="admin" if is_admin else "human",
            display_name=username.title(),
            auth_subject=f"local:{username}",
            is_active=True,
        )
        session.add(actor)
        session.flush()
        user = UserRecord(
            username=username,
            passcode_hash=hash_passcode(passcode),
            actor_id=actor.id,
            is_admin=is_admin,
            is_active=True,
        )
        session.add(user)
        session.commit()
        return user.id, actor.id


def _login(client, *, username: str, passcode: str) -> str:
    response = client.post(
        "/v1/auth/login",
        json={"username": username, "passcode": passcode},
    )
    assert response.status_code == 200, response.text
    csrf_token = client.cookies.get("csrf-token")
    assert csrf_token
    return csrf_token


def _passcode_hash(session_factory, username: str) -> str:
    with session_factory() as session:
        return str(
            session.scalar(
                sa.text("""
                    SELECT passcode_hash
                    FROM agenticqueue.users
                    WHERE username = :username
                    """),
                {"username": username},
            )
        )


def _session_count(session_factory, username: str) -> int:
    with session_factory() as session:
        return int(
            session.scalar(
                sa.text("""
                    SELECT count(*)
                    FROM agenticqueue.auth_sessions s
                    JOIN agenticqueue.users u ON u.id = s.user_id
                    WHERE u.username = :username
                    """),
                {"username": username},
            )
            or 0
        )


def _admin_actor_id(session_factory) -> uuid.UUID:
    with session_factory() as session:
        actor_id = session.scalar(sa.text("""
                SELECT actor_id
                FROM agenticqueue.users
                WHERE username = 'admin'
                """))
    assert isinstance(actor_id, uuid.UUID)
    return actor_id


def _auth_audit_rows(session_factory) -> list[dict[str, object]]:
    with session_factory() as session:
        return [dict(row) for row in session.execute(sa.text("""
                    SELECT user_id, actor_id, action, details
                    FROM agenticqueue.auth_audit_log
                    WHERE action = 'PASSCODE_RESET'
                    ORDER BY created_at ASC, id ASC
                    """)).mappings()]


def _table_counts(session_factory) -> dict[str, int]:
    with session_factory() as session:
        return {
            table: int(
                session.scalar(sa.text(f"SELECT count(*) FROM agenticqueue.{table}"))
                or 0
            )
            for table in ["users", "auth_sessions", "auth_audit_log"]
        }


def _idempotency_response_bodies(session_factory) -> list[str]:
    with session_factory() as session:
        return list(session.scalars(sa.text("""
                    SELECT response_body
                    FROM agenticqueue.idempotency_key
                    ORDER BY key
                    """)))


def _parse_cli_payload(result) -> dict[str, object]:
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload["passcode"], str)
    return payload


def test_cli_rotates_hash(session_factory) -> None:
    _seed_user(
        session_factory,
        username="alice",
        passcode="old-alice-passcode",
    )
    before_hash = _passcode_hash(session_factory, "alice")

    result = CliRunner().invoke(
        cli_app,
        ["reset-passcode", "--username", "alice"],
    )

    payload = _parse_cli_payload(result)
    new_passcode = str(payload["passcode"])
    after_hash = _passcode_hash(session_factory, "alice")
    assert after_hash != before_hash
    assert after_hash.startswith("$argon2id$")
    assert verify_passcode(new_passcode, after_hash) is True
    assert verify_passcode("old-alice-passcode", after_hash) is False
    assert result.output.count(new_passcode) == 1


def test_cli_kills_sessions(client, session_factory) -> None:
    _seed_user(
        session_factory,
        username="alice",
        passcode="old-alice-passcode",
    )
    _login(client, username="alice", passcode="old-alice-passcode")
    assert _session_count(session_factory, "alice") == 1

    result = CliRunner().invoke(
        cli_app,
        ["reset-passcode", "--username", "alice"],
    )

    payload = _parse_cli_payload(result)
    assert payload["sessions_deleted"] == 1
    assert _session_count(session_factory, "alice") == 0
    old_cookie_response = client.get("/v1/projects/mine")
    assert old_cookie_response.status_code == 401


def test_endpoint_requires_admin(client, session_factory) -> None:
    _seed_user(
        session_factory,
        username="alice",
        passcode="old-alice-passcode",
    )
    _seed_user(
        session_factory,
        username="bob",
        passcode="old-bob-passcode",
    )
    csrf_token = _login(client, username="admin", passcode="test-admin-passcode")

    admin_response = client.post(
        "/v1/auth/reset-passcode",
        headers={
            "Idempotency-Key": "00000000-0000-4000-8000-000000000001",
            "X-CSRF-Token": csrf_token,
        },
        json={"username": "alice"},
    )

    assert admin_response.status_code == 200, admin_response.text
    new_passcode = admin_response.json()["passcode"]
    assert verify_passcode(new_passcode, _passcode_hash(session_factory, "alice"))
    assert _idempotency_response_bodies(session_factory) == []

    client.cookies.clear()
    csrf_token = _login(client, username="bob", passcode="old-bob-passcode")
    non_admin_response = client.post(
        "/v1/auth/reset-passcode",
        headers={
            "Idempotency-Key": "00000000-0000-4000-8000-000000000002",
            "X-CSRF-Token": csrf_token,
        },
        json={"username": "alice"},
    )

    assert non_admin_response.status_code == 403


def test_audit_entry_written(client, session_factory) -> None:
    cli_target_id, cli_target_actor_id = _seed_user(
        session_factory,
        username="cli-target",
        passcode="old-cli-passcode",
    )
    api_target_id, _ = _seed_user(
        session_factory,
        username="api-target",
        passcode="old-api-passcode",
    )
    result = CliRunner().invoke(
        cli_app,
        ["reset-passcode", "--username", "cli-target"],
    )
    _parse_cli_payload(result)
    admin_actor_id = _admin_actor_id(session_factory)
    csrf_token = _login(client, username="admin", passcode="test-admin-passcode")

    response = client.post(
        "/v1/auth/reset-passcode",
        headers={
            "Idempotency-Key": "00000000-0000-4000-8000-000000000003",
            "X-CSRF-Token": csrf_token,
        },
        json={"username": "api-target"},
    )

    assert response.status_code == 200, response.text
    rows = _auth_audit_rows(session_factory)
    assert len(rows) == 2
    assert rows[0]["user_id"] == cli_target_id
    assert rows[0]["actor_id"] == cli_target_actor_id
    assert rows[0]["details"]["method"] == "cli"
    assert rows[0]["details"]["target_user_id"] == str(cli_target_id)
    assert rows[1]["user_id"] == api_target_id
    assert rows[1]["actor_id"] == admin_actor_id
    assert rows[1]["details"]["method"] == "api"
    assert rows[1]["details"]["target_user_id"] == str(api_target_id)


def test_cli_unknown_user(session_factory) -> None:
    before_counts = _table_counts(session_factory)

    result = CliRunner().invoke(
        cli_app,
        ["reset-passcode", "--username", "missing-user"],
    )

    assert result.exit_code == 2
    assert "Unknown user: missing-user" in result.output
    assert _table_counts(session_factory) == before_counts


def test_cli_requires_force_for_last_admin(session_factory) -> None:
    _seed_user(
        session_factory,
        username="admin",
        passcode="old-admin-passcode",
        is_admin=True,
    )
    before_hash = _passcode_hash(session_factory, "admin")

    result = CliRunner().invoke(
        cli_app,
        ["reset-passcode", "--username", "admin"],
    )

    assert result.exit_code == 2
    assert "requires --force" in result.output
    assert _passcode_hash(session_factory, "admin") == before_hash
