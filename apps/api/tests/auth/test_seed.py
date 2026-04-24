from __future__ import annotations

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from agenticqueue_api.app import create_app
from agenticqueue_api.auth.hashing import verify_passcode
from agenticqueue_api.models import ActorRecord, UserRecord


def _admin_hash(session_factory) -> str:
    with session_factory() as session:
        return str(
            session.scalar(
                sa.text(
                    "SELECT passcode_hash FROM agenticqueue.users WHERE username = 'admin'"
                )
            )
        )


def test_boot_without_passcode_fails(monkeypatch, session_factory) -> None:
    monkeypatch.delenv("AQ_ADMIN_PASSCODE", raising=False)
    monkeypatch.delenv("AGENTICQUEUE_ADMIN_PASSCODE", raising=False)
    app = create_app(session_factory=session_factory)

    with pytest.raises(RuntimeError, match="AQ_ADMIN_PASSCODE"):
        with TestClient(app, base_url="https://testserver"):
            pass


def test_boot_with_passcode_seeds_hashed_admin(monkeypatch, session_factory) -> None:
    monkeypatch.setenv("AQ_ADMIN_PASSCODE", "1234")
    app = create_app(session_factory=session_factory)

    with TestClient(app, base_url="https://testserver"):
        pass

    stored_hash = _admin_hash(session_factory)
    assert stored_hash.startswith("$argon2id$")
    assert stored_hash != "1234"
    assert verify_passcode("1234", stored_hash) is True


def test_boot_reuses_existing_admin_actor(monkeypatch, session_factory) -> None:
    with session_factory() as session:
        actor = ActorRecord(
            handle="admin",
            actor_type="admin",
            display_name="Existing Admin",
            auth_subject="existing-admin",
            is_active=True,
        )
        session.add(actor)
        session.commit()
        actor_id = actor.id

    monkeypatch.setenv("AQ_ADMIN_PASSCODE", "1234")
    app = create_app(session_factory=session_factory)

    with TestClient(app, base_url="https://testserver"):
        pass

    with session_factory() as session:
        user = session.scalar(
            sa.select(UserRecord).where(UserRecord.username == "admin")
        )

    assert user is not None
    assert user.actor_id == actor_id


def test_no_reseed_when_db_populated(monkeypatch, session_factory) -> None:
    monkeypatch.setenv("AQ_ADMIN_PASSCODE", "first-passcode")
    with TestClient(
        create_app(session_factory=session_factory),
        base_url="https://testserver",
    ):
        pass
    first_hash = _admin_hash(session_factory)

    monkeypatch.setenv("AQ_ADMIN_PASSCODE", "second-passcode")
    with TestClient(
        create_app(session_factory=session_factory),
        base_url="https://testserver",
    ):
        pass

    assert _admin_hash(session_factory) == first_hash
    assert verify_passcode("first-passcode", first_hash) is True
    assert verify_passcode("second-passcode", first_hash) is False
