from __future__ import annotations


def _wrong_login(client, ip: str = "203.0.113.10"):
    return client.post(
        "/v1/auth/login",
        headers={"X-Forwarded-For": ip},
        json={"username": "admin", "passcode": "wrong-passcode"},
    )


def test_sixth_wrong_login_attempt_returns_429(client) -> None:
    responses = [_wrong_login(client) for _ in range(6)]

    assert [response.status_code for response in responses[:5]] == [401] * 5
    assert responses[5].status_code == 429
    assert responses[5].headers["Retry-After"] == "900"


def test_login_rate_limit_survives_api_process_restart(session_factory) -> None:
    from fastapi.testclient import TestClient

    from agenticqueue_api.app import create_app

    with TestClient(
        create_app(session_factory=session_factory),
        base_url="https://testserver",
    ) as first_client:
        for _ in range(5):
            assert _wrong_login(first_client, ip="203.0.113.20").status_code == 401

    with TestClient(
        create_app(session_factory=session_factory),
        base_url="https://testserver",
    ) as second_client:
        response = _wrong_login(second_client, ip="203.0.113.20")

    assert response.status_code == 429
