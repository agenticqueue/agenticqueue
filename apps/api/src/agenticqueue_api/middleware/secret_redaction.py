"""Secret-detection middleware for mutating JSON payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import logging
import math
from pathlib import Path
import re
from typing import Any, Final, cast

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agenticqueue_api.errors import error_payload
from agenticqueue_api.policy import PolicyLoadError, PolicyRegistry

logger = logging.getLogger(__name__)

SECRET_BLOCKED_HEADER: Final = "X-Secret-Blocked"
_MUTATING_METHODS = frozenset({"POST", "PATCH"})
_JSON_PREFIXES = ("application/json", "application/merge-patch+json")
_HIGH_ENTROPY_ALLOWED_CHARS = re.compile(r"^[A-Za-z0-9+/=_-]{20,}$")
_HIGH_ENTROPY_TOKEN = re.compile(r"[A-Za-z0-9+/=_-]{20,}")
_UUID_LIKE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_ENGLISH_WORDS = {
    "about",
    "agent",
    "artifact",
    "audit",
    "block",
    "branch",
    "build",
    "check",
    "coding",
    "complete",
    "context",
    "create",
    "decision",
    "default",
    "deploy",
    "description",
    "example",
    "failure",
    "feature",
    "field",
    "learning",
    "middleware",
    "output",
    "payload",
    "policy",
    "project",
    "report",
    "review",
    "sample",
    "secret",
    "submit",
    "system",
    "task",
    "test",
    "token",
    "update",
    "valid",
    "value",
    "warning",
    "workspace",
}
_SECRET_HINTS = (
    "AKIA",
    "gh",
    "service_account",
    "PRIVATE KEY",
    "eyJ",
    "sk_live_",
    "xoxb-",
    "Bearer",
    "token=",
    "access_token=",
)


@dataclass(frozen=True)
class SecretMatch:
    """One detected secret span inside a string field."""

    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class SecretScanResult:
    """Result of scanning one JSON payload."""

    sanitized_payload: Any
    redaction_count: int
    types_matched: tuple[str, ...]


@dataclass(frozen=True)
class _PatternRule:
    kind: str
    pattern: re.Pattern[str]


_PATTERN_RULES = (
    _PatternRule(
        "aws_access_key",
        re.compile(r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])"),
    ),
    _PatternRule(
        "aws_secret_access_key",
        re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/=]{40}(?![A-Za-z0-9+/=])"),
    ),
    _PatternRule(
        "github_pat",
        re.compile(r"\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{16,}\b"),
    ),
    _PatternRule(
        "gcp_service_account",
        re.compile(
            r'"private_key_id"\s*:|"client_email"\s*:\s*"[^"]+@[^"]+\.iam\.gserviceaccount\.com"|'
            r'"type"\s*:\s*"service_account"',
            re.IGNORECASE,
        ),
    ),
    _PatternRule(
        "ssh_private_key",
        re.compile(
            r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----[\s\S]+?-----END(?: [A-Z0-9]+)* PRIVATE KEY-----"
        ),
    ),
    _PatternRule(
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    ),
    _PatternRule(
        "stripe_live_secret",
        re.compile(r"\bsk_live_[A-Za-z0-9]{16,}\b"),
    ),
    _PatternRule(
        "slack_bot_token",
        re.compile(r"\bxoxb-[A-Za-z0-9-]{16,}\b"),
    ),
    _PatternRule(
        "bearer_token_url",
        re.compile(
            r"https?://\S+[?&][^=\s]*(?:token|access_token|auth|authorization)="
            r"(?:Bearer(?:%20|\+)|Bearer\s+)[A-Za-z0-9._~+/=-]{8,}",
            re.IGNORECASE,
        ),
    ),
)


def compile_secret_pattern_rules(raw_rules: object) -> tuple[_PatternRule, ...]:
    """Compile custom detector rules from policy/body configuration."""

    if not isinstance(raw_rules, Sequence) or isinstance(
        raw_rules, (str, bytes, bytearray)
    ):
        return ()

    compiled: list[_PatternRule] = []
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, Mapping):
            continue

        raw_kind = raw_rule.get("kind", raw_rule.get("name"))
        raw_pattern = raw_rule.get("pattern")
        if not isinstance(raw_kind, str) or not isinstance(raw_pattern, str):
            continue

        kind = raw_kind.strip()
        pattern = raw_pattern.strip()
        if not kind or not pattern:
            continue

        try:
            compiled.append(_PatternRule(kind=kind, pattern=re.compile(pattern)))
        except re.error as error:
            logger.warning("invalid secret-redaction pattern %s: %s", kind, error)
    return tuple(compiled)


def _requires_secret_scan(scope: Scope) -> bool:
    if scope["type"] != "http":
        return False
    method = cast(str, scope["method"]).upper()
    if method not in _MUTATING_METHODS:
        return False
    path = cast(str, scope["path"])
    return path.startswith("/v1/") or path == "/task-types"


def _content_type(scope: Scope) -> str:
    for key, value in cast(list[tuple[bytes, bytes]], scope.get("headers", [])):
        if key.lower() == b"content-type":
            return value.decode("latin-1").split(";", 1)[0].strip().lower()
    return ""


def _request_looks_json(scope: Scope, body: bytes) -> bool:
    content_type = _content_type(scope)
    if any(content_type.startswith(prefix) for prefix in _JSON_PREFIXES):
        return True
    stripped = body.lstrip()
    return bool(stripped) and stripped[:1] in {b"{", b"["}


def _replace_content_length(scope: Scope, content_length: int) -> Scope:
    headers = []
    saw_content_length = False
    for key, value in cast(list[tuple[bytes, bytes]], scope.get("headers", [])):
        if key.lower() == b"content-length":
            headers.append((key, str(content_length).encode("latin-1")))
            saw_content_length = True
            continue
        headers.append((key, value))
    if not saw_content_length:
        headers.append((b"content-length", str(content_length).encode("latin-1")))

    updated = dict(scope)
    updated["headers"] = headers
    return cast(Scope, updated)


def shannon_entropy(value: str) -> float:
    """Return the Shannon entropy of a string."""

    if not value:
        return 0.0
    counts = {character: value.count(character) for character in set(value)}
    length = len(value)
    return -sum(
        (count / length) * math.log2(count / length) for count in counts.values()
    )


def has_dictionary_hit(value: str) -> bool:
    """Return whether a string contains an obvious English-word hit."""

    tokens = re.findall(r"[A-Za-z]{4,}", value.lower())
    return any(token in _ENGLISH_WORDS for token in tokens)


def _looks_generic_high_entropy_secret(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) < 20:
        return False
    if any(separator in stripped for separator in (" ", "\n", "\r", "/", "\\", "://")):
        return False
    if _UUID_LIKE.fullmatch(stripped):
        return False
    if not _HIGH_ENTROPY_ALLOWED_CHARS.fullmatch(stripped):
        return False
    if has_dictionary_hit(stripped):
        return False
    return shannon_entropy(stripped) > 4.5


@lru_cache(maxsize=4096)
def _might_contain_secret(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if any(hint in value for hint in _SECRET_HINTS):
        return True
    if len(stripped) < 20:
        return False
    if _UUID_LIKE.fullmatch(stripped):
        return False
    if _HIGH_ENTROPY_ALLOWED_CHARS.fullmatch(stripped):
        return True

    candidate = _HIGH_ENTROPY_TOKEN.search(value)
    if candidate is None:
        return False
    return not _UUID_LIKE.fullmatch(candidate.group(0))


def payload_might_contain_secret(
    payload: Any,
    *,
    extra_rules: Sequence[_PatternRule] = (),
) -> bool:
    """Cheap preflight for whether a payload is worth a full secret scan."""

    if isinstance(payload, Mapping):
        return any(
            payload_might_contain_secret(value, extra_rules=extra_rules)
            for value in payload.values()
        )
    if isinstance(payload, Sequence) and not isinstance(
        payload, (str, bytes, bytearray)
    ):
        return any(
            payload_might_contain_secret(value, extra_rules=extra_rules)
            for value in payload
        )
    if isinstance(payload, str):
        if extra_rules and any(rule.pattern.search(payload) for rule in extra_rules):
            return True
        return _might_contain_secret(payload)
    if hasattr(payload, "__dict__"):
        return any(
            payload_might_contain_secret(value, extra_rules=extra_rules)
            for key, value in vars(payload).items()
            if not key.startswith("_")
        )
    return False


def find_secret_matches(
    value: str,
    *,
    extra_rules: Sequence[_PatternRule] = (),
) -> list[SecretMatch]:
    """Return all non-overlapping secret matches within one string."""

    if not extra_rules and not _might_contain_secret(value):
        return []

    matches: list[SecretMatch] = []
    for rule in (*_PATTERN_RULES, *extra_rules):
        for matched in rule.pattern.finditer(value):
            start, end = matched.span()
            matches.append(SecretMatch(kind=rule.kind, start=start, end=end))

    if matches:
        return sorted(matches, key=lambda match: match.start)

    if _looks_generic_high_entropy_secret(value):
        return [SecretMatch(kind="generic_high_entropy", start=0, end=len(value))]

    return []


def _apply_redactions(value: str, matches: Sequence[SecretMatch]) -> str:
    pieces: list[str] = []
    cursor = 0
    for match in sorted(matches, key=lambda item: item.start):
        if match.start < cursor:
            continue
        pieces.append(value[cursor : match.start])
        pieces.append(f"[REDACTED:{match.kind}]")
        cursor = match.end
    pieces.append(value[cursor:])
    return "".join(pieces)


def scan_json_payload(
    payload: Any,
    *,
    hard_block: bool,
    extra_rules: Sequence[_PatternRule] = (),
) -> SecretScanResult:
    """Scan a JSON-like payload, optionally redacting matched strings."""

    collected: list[SecretMatch] = []

    def _walk(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: _walk(item) for key, item in value.items()}
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            return [_walk(item) for item in value]
        if not isinstance(value, str):
            return value

        matches = find_secret_matches(value, extra_rules=extra_rules)
        if not matches:
            return value

        collected.extend(matches)
        if hard_block:
            return value
        return _apply_redactions(value, matches)

    sanitized_payload = _walk(payload)
    ordered_types = tuple(dict.fromkeys(match.kind for match in collected))
    return SecretScanResult(
        sanitized_payload=sanitized_payload,
        redaction_count=len(collected),
        types_matched=ordered_types,
    )


class SecretRedactionMiddleware:
    """Block or redact known secrets before request payloads hit persistence."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        policy_directory: Path | None = None,
        policy_pack_name: str = "default-coding",
        hard_block_default: bool = True,
    ) -> None:
        self.app = app
        self.policy_pack_name = policy_pack_name
        self.hard_block_default = hard_block_default
        self.policy_registry: PolicyRegistry | None = None

        if policy_directory is None:
            return

        registry = PolicyRegistry(policy_directory)
        try:
            registry.load()
        except PolicyLoadError as error:
            logger.warning("secret-redaction policy load failed: %s", error)
            return
        self.policy_registry = registry

    def _hard_block_secrets(self) -> bool:
        if self.policy_registry is None:
            return self.hard_block_default
        try:
            body = self.policy_registry.get(self.policy_pack_name).body
        except PolicyLoadError as error:
            logger.warning("secret-redaction policy lookup failed: %s", error)
            return self.hard_block_default

        configured = body.get("hard_block_secrets", self.hard_block_default)
        return bool(configured)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _requires_secret_scan(scope):
            await self.app(scope, receive, send)
            return

        buffered_body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return

            buffered_body.extend(cast(bytes, message.get("body", b"")))
            if not cast(bool, message.get("more_body", False)):
                break

        body = bytes(buffered_body)
        if not body or not _request_looks_json(scope, body):
            await self._forward(scope, body, send)
            return

        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            await self._forward(scope, body, send)
            return

        hard_block = self._hard_block_secrets()
        scan = scan_json_payload(payload, hard_block=hard_block)
        if scan.redaction_count == 0:
            await self._forward(scope, body, send)
            return

        original_sha256 = hashlib.sha256(body).hexdigest()
        types_csv = ",".join(scan.types_matched)

        if hard_block:
            response = JSONResponse(
                status_code=400,
                content=error_payload(
                    status_code=400,
                    message="Request payload contains secret material",
                    details={
                        "redaction_count": scan.redaction_count,
                        "types_matched": list(scan.types_matched),
                    },
                ),
                headers={SECRET_BLOCKED_HEADER: scan.types_matched[0]},
            )
            await response(scope, self._empty_receive, send)
            return

        logger.warning(
            "secret-redaction applied: count=%s types=%s sha256=%s",
            scan.redaction_count,
            types_csv,
            original_sha256,
        )

        redacted_body = json.dumps(
            scan.sanitized_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        updated_scope = _replace_content_length(scope, len(redacted_body))
        state = cast(dict[str, Any], updated_scope.setdefault("state", {}))
        state["secret_redaction_context"] = {
            "redaction_count": scan.redaction_count,
            "types_matched": list(scan.types_matched),
            "original_sha256": original_sha256,
        }
        await self._forward(cast(Scope, updated_scope), redacted_body, send)

    async def _forward(self, scope: Scope, body: bytes, send: Send) -> None:
        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_receive, send)

    async def _empty_receive(self) -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}
