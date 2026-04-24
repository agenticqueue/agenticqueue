from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
import uuid

from agenticqueue_api.middleware import secret_redaction as secret_redaction_module
from agenticqueue_api.middleware.secret_redaction import (
    SecretMatch,
    _apply_redactions,
    _looks_generic_high_entropy_secret,
    _replace_content_length,
    _request_looks_json,
    compile_secret_pattern_rules,
    find_secret_matches,
    has_dictionary_hit,
    payload_might_contain_secret,
    scan_json_payload,
    shannon_entropy,
)
from tests.secret_redaction_support import (
    clean_corpus,
    fake_aws_access_key,
    fake_aws_secret_access_key,
    fake_github_pat,
    fake_slack_bot_token,
    fake_stripe_live_secret,
    secret_corpus,
)

_SHARED_SUPPORT_NAMES = {
    "build_app",
    "clean_corpus",
    "fake_aws_access_key",
    "fake_aws_secret_access_key",
    "fake_github_pat",
    "fake_slack_bot_token",
    "fake_stripe_live_secret",
    "policy_dir",
    "secret_corpus",
}
_SECURITY_ONLY_TESTS = {
    "test_secret_redaction_blocks_payload_when_policy_enables_hard_block",
    "test_secret_redaction_rewrites_payload_and_sets_request_context",
    "test_secret_redaction_skips_get_invalid_json_and_missing_policy",
    "test_secret_redaction_handles_list_root_payloads",
    "test_secret_redaction_forwards_non_json_and_safe_payloads",
    "test_secret_redaction_internal_async_paths_cover_disconnect_and_replay",
    "test_secret_redaction_missing_policy_pack_falls_back_to_default",
}


def _function_names(path: Path) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in module.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    }


def test_shared_support_module_owns_secret_redaction_fixtures() -> None:
    tests_root = Path(__file__).resolve().parents[1]
    support_path = tests_root / "secret_redaction_support.py"
    security_path = tests_root / "security" / "firewall" / "test_secret_redaction.py"

    assert support_path.exists()
    support_names = _function_names(support_path)
    assert _SHARED_SUPPORT_NAMES <= support_names
    assert not (_SHARED_SUPPORT_NAMES & _function_names(Path(__file__).resolve()))
    assert not (_SHARED_SUPPORT_NAMES & _function_names(security_path))


def test_security_suite_retains_http_integration_only_secret_redaction_proofs() -> None:
    tests_root = Path(__file__).resolve().parents[1]
    security_path = tests_root / "security" / "firewall" / "test_secret_redaction.py"

    unit_names = _function_names(Path(__file__).resolve())
    security_names = _function_names(security_path)

    assert not (_SECURITY_ONLY_TESTS & unit_names)
    assert _SECURITY_ONLY_TESTS <= security_names


def test_find_secret_matches_covers_known_patterns_and_generic_entropy() -> None:
    cases = {
        "aws_access_key": f"deploy with key {fake_aws_access_key()} immediately",
        "aws_secret_access_key": fake_aws_secret_access_key(),
        "github_pat": fake_github_pat(),
        "gcp_service_account": '{"type":"service_account","private_key_id":"abc123"}',
        "ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----",
        "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.signaturepart",
        "stripe_live_secret": fake_stripe_live_secret(),
        "slack_bot_token": fake_slack_bot_token(),
        "bearer_token_url": "https://example.com/hook?access_token=Bearer%20abcdEFGH1234",
        "generic_high_entropy": "Q29kZXhTZWNyZXQtVG9rZW4tQUJDREVGR0hJSktMTU5PUFFSU1RVVldY",
    }

    for expected_kind, sample in cases.items():
        matches = find_secret_matches(sample)
        assert matches
        assert matches[0].kind == expected_kind


def test_entropy_and_dictionary_helpers_discriminate_clean_text() -> None:
    assert shannon_entropy("") == 0.0
    assert shannon_entropy("aaaaabbbbb") < shannon_entropy("abc123XYZ+/=")
    assert has_dictionary_hit("artifact review payload warning")
    assert not find_secret_matches("artifact review payload warning for the next task")
    assert not find_secret_matches("artifact123review123build")
    assert not find_secret_matches(str(uuid.uuid4()))
    assert not find_secret_matches("abcdefghijklmnopqrst....")


def test_request_and_header_helpers_cover_fallback_paths() -> None:
    scope = {"type": "http", "method": "POST", "path": "/v1/tasks", "headers": []}
    assert secret_redaction_module._content_type(scope) == ""
    assert _request_looks_json(scope, b'{"description":"hello"}')
    updated = _replace_content_length(scope, 17)
    assert (b"content-length", b"17") in updated["headers"]


def test_custom_rule_compilation_and_payload_preflight_cover_guard_paths() -> None:
    assert compile_secret_pattern_rules("not-a-sequence") == ()

    compiled = compile_secret_pattern_rules(
        [
            None,
            {"name": "custom_secret", "pattern": "ZXCVSECRET"},
            {"kind": "wrong_type", "pattern": 123},
            {"kind": "   ", "pattern": "still-ignored"},
            {"kind": "broken", "pattern": "("},
        ]
    )
    assert [rule.kind for rule in compiled] == ["custom_secret"]

    class PayloadObject:
        def __init__(self) -> None:
            self.note = "ZXCVSECRET"
            self._ignored = "ZXCVSECRET"

    assert payload_might_contain_secret(
        {"items": ["plain", {"note": "ZXCVSECRET"}]},
        extra_rules=compiled,
    )
    assert payload_might_contain_secret(PayloadObject(), extra_rules=compiled)
    assert not payload_might_contain_secret("")
    assert not payload_might_contain_secret(123)


def test_generic_entropy_guard_paths_reject_short_and_uuid_values() -> None:
    assert not _looks_generic_high_entropy_secret("short-token")
    assert not _looks_generic_high_entropy_secret(
        "550e8400-e29b-41d4-a716-446655440000"
    )


def test_apply_redactions_skips_overlapping_matches() -> None:
    value = "abcdefghij"
    redacted = _apply_redactions(
        value,
        [
            SecretMatch(kind="alpha", start=0, end=5),
            SecretMatch(kind="beta", start=3, end=8),
        ],
    )
    assert redacted == "[REDACTED:alpha]fghij"


def test_scan_json_payload_redacts_nested_values_and_counts_matches() -> None:
    payload = {
        "description": f"Use {fake_aws_access_key()} and {fake_github_pat()}",
        "nested": [
            "plain text",
            {"token": fake_slack_bot_token()},
        ],
    }

    result = scan_json_payload(payload, hard_block=False)

    assert result.redaction_count == 3
    assert result.types_matched == (
        "aws_access_key",
        "github_pat",
        "slack_bot_token",
    )
    assert (
        result.sanitized_payload["description"]
        == "Use [REDACTED:aws_access_key] and [REDACTED:github_pat]"
    )
    assert result.sanitized_payload["nested"][0] == "plain text"
    assert (
        result.sanitized_payload["nested"][1]["token"] == "[REDACTED:slack_bot_token]"
    )


def test_scan_json_payload_hard_block_mode_leaves_payload_unmodified() -> None:
    payload = {"description": fake_aws_access_key()}

    result = scan_json_payload(payload, hard_block=True)

    assert result.redaction_count == 1
    assert result.types_matched == ("aws_access_key",)
    assert result.sanitized_payload == payload


def test_scan_json_payload_preserves_non_string_scalars() -> None:
    payload = {"count": 7, "enabled": True, "nested": [None, 3.14]}

    result = scan_json_payload(payload, hard_block=False)

    assert result.redaction_count == 0
    assert result.types_matched == ()
    assert result.sanitized_payload == payload


def test_secret_corpus_detects_all_50_payloads() -> None:
    corpus = secret_corpus()
    assert len(corpus) == 50

    detected = 0
    for expected_kind, payload in corpus:
        result = scan_json_payload(payload, hard_block=False)
        if expected_kind in result.types_matched:
            detected += 1

    assert detected == 50


def test_clean_corpus_false_positive_rate_stays_below_one_percent() -> None:
    corpus = clean_corpus()
    assert len(corpus) == 500

    false_positives = 0
    for payload in corpus:
        result = scan_json_payload(payload, hard_block=False)
        if result.redaction_count:
            false_positives += 1

    assert false_positives / len(corpus) < 0.01
