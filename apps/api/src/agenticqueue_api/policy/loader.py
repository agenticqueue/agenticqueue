"""Policy pack loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agenticqueue_api.capability_keys import CapabilityKey

POLICY_SUFFIX = ".policy.yaml"
POLICY_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class PolicyLoadError(ValueError):
    """Raised when policy packs cannot be loaded from disk."""


class _PolicyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    hitl_required: bool
    autonomy_tier: int = Field(ge=1, le=5)
    capabilities: list[CapabilityKey] = Field(default_factory=list)
    body: dict[str, Any] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("version must not be empty")
        return normalized

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, values: list[CapabilityKey]) -> list[CapabilityKey]:
        deduped: list[CapabilityKey] = []
        seen: set[CapabilityKey] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            deduped.append(value)
        return deduped

    @field_validator("body", mode="before")
    @classmethod
    def validate_body(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("body must be an object")
        return dict(value)


@dataclass(frozen=True)
class PolicyPack:
    """One immutable policy pack loaded from disk."""

    name: str
    version: str
    hitl_required: bool
    autonomy_tier: int
    capabilities: tuple[CapabilityKey, ...]
    body: dict[str, Any]
    path: Path


def _policy_name_from_path(path: Path) -> str:
    name = path.name[: -len(POLICY_SUFFIX)]
    if not POLICY_NAME_RE.fullmatch(name):
        raise PolicyLoadError(f"Invalid policy file name: {path.name}")
    return name


def load_policy_pack(path: Path) -> PolicyPack:
    """Load one policy pack file from disk."""

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise PolicyLoadError(f"Invalid policy file {path.name}: {error}") from error

    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise PolicyLoadError(f"Policy file {path.name} must contain a YAML mapping")

    parsed = _PolicyPayload.model_validate(payload)
    return PolicyPack(
        name=_policy_name_from_path(path),
        version=parsed.version,
        hitl_required=parsed.hitl_required,
        autonomy_tier=parsed.autonomy_tier,
        capabilities=tuple(parsed.capabilities),
        body=parsed.body,
        path=path,
    )


class PolicyRegistry:
    """Filesystem-backed policy registry."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._packs: dict[str, PolicyPack] = {}

    def load(self) -> None:
        """Load every policy file in the configured directory."""

        if not self.directory.exists():
            raise PolicyLoadError(f"Policy directory not found: {self.directory}")

        policy_paths = sorted(self.directory.glob(f"*{POLICY_SUFFIX}"))
        if not policy_paths:
            raise PolicyLoadError(f"No policy files found in {self.directory}")

        self._packs = {}
        for policy_path in policy_paths:
            pack = load_policy_pack(policy_path)
            self._packs[pack.name] = pack

    def get(self, name: str) -> PolicyPack:
        """Return one policy pack by name."""

        pack = self._packs.get(name)
        if pack is None:
            raise PolicyLoadError(f"Unknown policy pack: {name}")
        return pack

    def list(self) -> list[PolicyPack]:
        """Return every loaded policy pack sorted by name."""

        return [self._packs[name] for name in sorted(self._packs)]
