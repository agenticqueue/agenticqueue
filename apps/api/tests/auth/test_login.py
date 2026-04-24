from __future__ import annotations

import sqlalchemy as sa


def test_login_sets_secure_httponly_lax_session_cookie(client, session_factory) -> None:
    response = client.post(
        "/v1/auth/login",
        json={"username": "admin", "passcode": "test-admin-passcode"},
    )

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert "aq_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=Lax" in set_cookie
    assert "Path=/" in set_cookie
    assert "Max-Age=604800" in set_cookie
    assert "csrf-token=" in set_cookie

    with session_factory() as session:
        passcode_hash = session.scalar(
            sa.text(
                "SELECT passcode_hash FROM agenticqueue.users WHERE username = 'admin'"
            )
        )
    assert str(passcode_hash).startswith("$argon2id$")


def test_login_rejects_wrong_passcode(client) -> None:
    response = client.post(
        "/v1/auth/login",
        json={"username": "admin", "passcode": "wrong-passcode"},
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "auth_failed"
