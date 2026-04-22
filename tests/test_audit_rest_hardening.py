from scripts import audit_rest_hardening as audit_rest_hardening


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
