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
