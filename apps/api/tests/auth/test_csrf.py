from __future__ import annotations


def _login(client) -> str:
    response = client.post(
        "/v1/auth/login",
        json={"username": "admin", "passcode": "test-admin-passcode"},
    )
    assert response.status_code == 200
    csrf_token = client.cookies.get("csrf-token")
    assert csrf_token
    return csrf_token


def test_csrf_valid_header_match_allows_mutating_request(client) -> None:
    csrf_token = _login(client)

    response = client.post(
        "/v1/auth/logout",
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200


def test_csrf_missing_header_rejects_mutating_request(client) -> None:
    _login(client)

    response = client.post("/v1/auth/logout")

    assert response.status_code == 403
    assert response.json()["error_code"] == "forbidden"


def test_csrf_mismatched_header_rejects_mutating_request(client) -> None:
    _login(client)

    response = client.post(
        "/v1/auth/logout",
        headers={"X-CSRF-Token": "not-the-cookie"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "forbidden"


def test_csrf_forged_cookie_header_pair_rejects_mutating_request(client) -> None:
    _login(client)
    client.cookies.set("csrf-token", "attacker-token")

    response = client.post(
        "/v1/auth/logout",
        headers={"X-CSRF-Token": "attacker-token"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "forbidden"


def test_csrf_does_not_require_header_for_get(client) -> None:
    _login(client)

    response = client.get("/v1/projects/mine")

    assert response.status_code == 200
