import asyncio
from importlib.util import module_from_spec
from importlib.util import spec_from_file_location
from pathlib import Path
import sys
from typing import Any


def _load_audit_module() -> Any:
    spec = spec_from_file_location(
        "aq_audit_rest_hardening",
        Path(__file__).resolve().parents[1] / "scripts" / "audit_rest_hardening.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit_rest_hardening = _load_audit_module()


def test_summarize_latency_metrics_handles_empty_samples() -> None:
    p50, p99, failure = audit_rest_hardening._summarize_latency_metrics([])

    assert p50 == 0.0
    assert p99 == 0.0
    assert failure == "No successful latency samples were captured during the soak."


def test_summarize_latency_metrics_uses_small_sample_fallbacks() -> None:
    p50, p99, failure = audit_rest_hardening._summarize_latency_metrics(
        [12.5, 25.0, 50.0]
    )

    assert p50 == 25.0
    assert p99 == 50.0
    assert failure is None


def test_resolve_soak_config_uses_full_profile_outside_ci(monkeypatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("SOAK_CI_MODE", raising=False)

    config = audit_rest_hardening._resolve_soak_config(
        duration_seconds=300,
        actor_count=100,
        rps_per_actor=10.0,
    )

    assert config.ci_mode_enabled is False
    assert config.effective_duration_seconds == 300
    assert config.effective_actor_count == 100
    assert config.effective_rps_per_actor == 10.0


def test_resolve_soak_config_clamps_ci_profile(monkeypatch) -> None:
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("SOAK_CI_MODE", raising=False)

    config = audit_rest_hardening._resolve_soak_config(
        duration_seconds=300,
        actor_count=100,
        rps_per_actor=10.0,
    )

    assert config.ci_mode_enabled is True
    assert (
        config.effective_duration_seconds
        == audit_rest_hardening.CI_SOAK_DURATION_SECONDS
    )
    assert config.effective_actor_count == audit_rest_hardening.CI_SOAK_ACTOR_COUNT
    assert config.effective_rps_per_actor == audit_rest_hardening.CI_SOAK_RPS_PER_ACTOR


def test_resolve_soak_config_allows_explicit_ci_override(monkeypatch) -> None:
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("SOAK_CI_MODE", "false")

    config = audit_rest_hardening._resolve_soak_config(
        duration_seconds=300,
        actor_count=100,
        rps_per_actor=10.0,
    )

    assert config.ci_mode_enabled is False
    assert config.effective_duration_seconds == 300
    assert config.effective_actor_count == 100
    assert config.effective_rps_per_actor == 10.0


def test_soak_actor_records_timeout_exception(monkeypatch) -> None:
    class HangingClient:
        async def get(self, _endpoint: str, *, headers: dict[str, str]) -> None:
            del headers
            await asyncio.sleep(1)

    monkeypatch.setattr(audit_rest_hardening, "SOAK_REQUEST_TIMEOUT_SECONDS", 0.01)
    metrics = {
        "latencies_ms": [],
        "request_count": 0,
        "server_errors": 0,
        "rate_limited": 0,
        "other_errors": [],
        "request_exceptions": [],
        "timed_out_actors": 0,
    }

    asyncio.run(
        audit_rest_hardening._soak_actor(
            actor_index=0,
            token="test-token",
            duration_seconds=0.001,
            rps_per_actor=1000.0,
            metrics=metrics,
            client=HangingClient(),  # type: ignore[arg-type]
        )
    )

    assert metrics["latencies_ms"] == []
    assert metrics["request_count"] == 0
    assert metrics["request_exceptions"] == [
        {
            "error_type": "TimeoutError",
            "message": "/v1/workspaces?limit=1 exceeded the 0.0s request budget during the soak.",
        }
    ]
