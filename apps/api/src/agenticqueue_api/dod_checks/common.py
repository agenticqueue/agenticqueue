"""Shared DoD check models and helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Protocol

DEFAULT_CHECK_TIMEOUT_SECONDS = 30.0


class DodCheckValidationError(ValueError):
    """Raised when a declarative DoD check cannot be parsed or executed."""


class DodItemState(StrEnum):
    """Supported DoD item outcomes."""

    CHECKED = "checked"
    PARTIAL = "partial"
    UNCHECKED_BLOCKED = "unchecked_blocked"
    UNCHECKED_UNMET = "unchecked_unmet"


@dataclass(frozen=True)
class DodCheckDefinition:
    """One declarative DoD check definition from a task contract."""

    item: str
    check_type: str
    fields: dict[str, Any]
    timeout_seconds: float


@dataclass(frozen=True)
class DodCheckResult:
    """Outcome of one low-level DoD check."""

    item: str
    check_type: str
    state: DodItemState
    note: str


@dataclass(frozen=True)
class ArtifactFile:
    """One artifact declared in a submission bundle."""

    uri: str
    path: Path
    details: dict[str, Any]


@dataclass(frozen=True)
class ArtifactBundle:
    """Submission artifact bundle exposed to DoD handlers."""

    files: dict[str, ArtifactFile]

    @classmethod
    def from_output(
        cls,
        output: Mapping[str, Any],
        *,
        artifact_root: Path | None = None,
    ) -> ArtifactBundle:
        raw_artifacts = output.get("artifacts")
        if not isinstance(raw_artifacts, list):
            raise DodCheckValidationError(
                "Submission output must include an 'artifacts' list before running DoD checks."
            )

        files: dict[str, ArtifactFile] = {}
        for index, artifact in enumerate(raw_artifacts):
            if not isinstance(artifact, Mapping):
                raise DodCheckValidationError(
                    f"Artifact entry {index} must be an object with a uri."
                )

            uri = artifact.get("uri")
            if not isinstance(uri, str) or not uri.strip():
                raise DodCheckValidationError(
                    f"Artifact entry {index} must declare a non-empty uri."
                )

            raw_details = artifact.get("details", {})
            details = dict(raw_details) if isinstance(raw_details, Mapping) else {}
            path = Path(uri)
            if artifact_root is not None and not path.is_absolute():
                path = artifact_root / path

            files[uri] = ArtifactFile(uri=uri, path=path, details=details)

        test_report = output.get("test_report")
        if (
            isinstance(test_report, str)
            and test_report.strip()
            and test_report not in files
        ):
            path = Path(test_report)
            if artifact_root is not None and not path.is_absolute():
                path = artifact_root / path
            files[test_report] = ArtifactFile(
                uri=test_report,
                path=path,
                details={"kind": "test-report"},
            )

        return cls(files=files)


class GitHubClientProtocol(Protocol):
    """Minimal GitHub client surface for DoD checks."""

    def get_check_conclusion(
        self,
        *,
        repo: str,
        sha: str,
        check_name: str,
        timeout_seconds: float,
    ) -> str | None: ...

    def get_pull_request_mergeable(
        self,
        *,
        repo: str,
        pr_number: int,
        timeout_seconds: float,
    ) -> bool | None: ...


@dataclass(frozen=True)
class DodCheckContext:
    """Dependencies shared across DoD checks."""

    bundle: ArtifactBundle
    registry: Any
    github_client: GitHubClientProtocol | None = None


def coerce_check_definition(raw: Any) -> DodCheckDefinition:
    """Validate one raw DoD check mapping from a task contract."""

    if not isinstance(raw, Mapping):
        raise DodCheckValidationError("Each DoD check must be an object.")

    item = _require_non_empty_string(raw, "item")
    check_type = _require_non_empty_string(raw, "type")
    if check_type == "shell":
        raise DodCheckValidationError("shell exec disabled by task policy")

    timeout_value = raw.get("timeout_seconds", DEFAULT_CHECK_TIMEOUT_SECONDS)
    if not isinstance(timeout_value, (int, float)) or timeout_value <= 0:
        raise DodCheckValidationError(
            "DoD checks must declare a positive timeout_seconds value."
        )

    return DodCheckDefinition(
        item=item,
        check_type=check_type,
        fields=dict(raw),
        timeout_seconds=float(timeout_value),
    )


def require_string(definition: DodCheckDefinition, field: str) -> str:
    """Return one required non-empty string field."""

    return _require_non_empty_string(definition.fields, field)


def optional_string(
    definition: DodCheckDefinition,
    field: str,
    *,
    default: str | None = None,
) -> str | None:
    """Return one optional string field."""

    value = definition.fields.get(field, default)
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise DodCheckValidationError(
            f"DoD check '{definition.check_type}' requires '{field}' to be a non-empty string."
        )
    return value.strip()


def require_int(
    definition: DodCheckDefinition,
    field: str,
    *,
    minimum: int | None = None,
) -> int:
    """Return one required integer field."""

    value = definition.fields.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DodCheckValidationError(
            f"DoD check '{definition.check_type}' requires '{field}' to be an integer."
        )
    if minimum is not None and value < minimum:
        raise DodCheckValidationError(
            f"DoD check '{definition.check_type}' requires '{field}' >= {minimum}."
        )
    return value


def optional_int(
    definition: DodCheckDefinition,
    field: str,
    *,
    minimum: int | None = None,
) -> int | None:
    """Return one optional integer field."""

    value = definition.fields.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise DodCheckValidationError(
            f"DoD check '{definition.check_type}' requires '{field}' to be an integer."
        )
    if minimum is not None and value < minimum:
        raise DodCheckValidationError(
            f"DoD check '{definition.check_type}' requires '{field}' >= {minimum}."
        )
    return value


def select_artifacts(
    bundle: ArtifactBundle,
    *,
    path_expr: str,
    path_mode: str = "exact",
) -> tuple[ArtifactFile, ...]:
    """Return artifacts matching one URI expression."""

    if path_mode == "exact":
        artifact = bundle.files.get(path_expr)
        return () if artifact is None else (artifact,)

    uris = bundle.files.values()
    if path_mode == "glob":
        return tuple(
            artifact for artifact in uris if fnmatchcase(artifact.uri, path_expr)
        )

    if path_mode == "regex":
        try:
            matcher = re.compile(path_expr)
        except re.error as error:
            raise DodCheckValidationError(
                f"Invalid artifact path regex '{path_expr}': {error.msg}"
            ) from error
        return tuple(
            artifact for artifact in uris if matcher.search(artifact.uri) is not None
        )

    raise DodCheckValidationError(f"Unsupported path_mode '{path_mode}'.")


def read_text(artifact: ArtifactFile) -> str:
    """Read one artifact as UTF-8 text."""

    return artifact.path.read_text(encoding="utf-8")


def _require_non_empty_string(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DodCheckValidationError(
            f"DoD checks require '{field}' to be a non-empty string."
        )
    return value.strip()
