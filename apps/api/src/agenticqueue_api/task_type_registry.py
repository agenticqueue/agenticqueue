"""Filesystem-backed task type registry and schema loader."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

import yaml  # type: ignore[import-untyped]
from jsonschema import SchemaError  # type: ignore[import-untyped]
from jsonschema.validators import Draft202012Validator  # type: ignore[import-untyped]

SCHEMA_SUFFIX = ".schema.json"
POLICY_SUFFIX = ".policy.yaml"
TASK_TYPE_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SchemaLoadError(ValueError):
    """Raised when task type files cannot be loaded or validated."""


@dataclass(frozen=True)
class TaskTypeDefinition:
    """Loaded task type payload and its backing files."""

    name: str
    schema: dict[str, Any]
    policy: dict[str, Any]
    schema_path: Path
    policy_path: Path


def _validate_task_type_name(name: str) -> str:
    normalized = name.strip()
    if not TASK_TYPE_NAME_RE.fullmatch(normalized):
        raise SchemaLoadError(f"Invalid task type name: {name!r}")
    return normalized


def _load_json_dict(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SchemaLoadError(f"Invalid schema file {path.name}: {error}") from error

    if not isinstance(payload, dict):
        raise SchemaLoadError(f"Schema file {path.name} must contain a JSON object")

    try:
        Draft202012Validator.check_schema(payload)
    except SchemaError as error:
        raise SchemaLoadError(
            f"Invalid schema file {path.name}: {error.message}"
        ) from error
    return payload


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise SchemaLoadError(f"Invalid policy file {path.name}: {error}") from error

    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise SchemaLoadError(f"Policy file {path.name} must contain a YAML mapping")
    return payload


class TaskTypeRegistry:
    """Load, register, and optionally hot-reload task types from disk."""

    def __init__(self, directory: Path, *, reload_enabled: bool = False) -> None:
        self.directory = directory
        self.reload_enabled = reload_enabled
        self._definitions: dict[str, TaskTypeDefinition] = {}
        self._signature: tuple[tuple[str, int, int], ...] = ()
        self._lock = RLock()

    def _directory_signature(self) -> tuple[tuple[str, int, int], ...]:
        if not self.directory.exists():
            return ()

        relevant_files: list[tuple[str, int, int]] = []
        for path in sorted(self.directory.iterdir()):
            if not path.is_file():
                continue
            if not (
                path.name.endswith(SCHEMA_SUFFIX) or path.name.endswith(POLICY_SUFFIX)
            ):
                continue
            stat = path.stat()
            relevant_files.append((path.name, stat.st_mtime_ns, stat.st_size))
        return tuple(relevant_files)

    def load(self) -> None:
        """Load all task types from disk, validating schema and policy pairs."""

        with self._lock:
            if not self.directory.exists():
                raise SchemaLoadError(
                    f"Task type directory not found: {self.directory}"
                )

            schema_paths = sorted(self.directory.glob(f"*{SCHEMA_SUFFIX}"))
            if not schema_paths:
                raise SchemaLoadError(
                    f"No task type schema files found in {self.directory}"
                )

            definitions: dict[str, TaskTypeDefinition] = {}
            for schema_path in schema_paths:
                name = _validate_task_type_name(schema_path.name[: -len(SCHEMA_SUFFIX)])
                policy_path = self.directory / f"{name}{POLICY_SUFFIX}"
                if not policy_path.exists():
                    raise SchemaLoadError(
                        f"Missing policy file for task type {name}: {policy_path.name}"
                    )
                definitions[name] = TaskTypeDefinition(
                    name=name,
                    schema=_load_json_dict(schema_path),
                    policy=_load_yaml_dict(policy_path),
                    schema_path=schema_path,
                    policy_path=policy_path,
                )

            policy_paths = sorted(self.directory.glob(f"*{POLICY_SUFFIX}"))
            known_names = set(definitions)
            for policy_path in policy_paths:
                name = _validate_task_type_name(policy_path.name[: -len(POLICY_SUFFIX)])
                if name not in known_names:
                    raise SchemaLoadError(
                        f"Policy file has no matching schema: {policy_path.name}"
                    )

            self._definitions = definitions
            self._signature = self._directory_signature()

    def maybe_reload(self) -> bool:
        """Reload task type files when dev reload is enabled and files changed."""

        if not self.reload_enabled:
            return False

        with self._lock:
            if self._directory_signature() == self._signature:
                return False
            self.load()
            return True

    def list(self) -> list[TaskTypeDefinition]:
        """Return every known task type, reloading first in dev mode."""

        self.maybe_reload()
        with self._lock:
            return [self._definitions[name] for name in sorted(self._definitions)]

    def register(
        self,
        *,
        name: str,
        schema: dict[str, Any],
        policy: dict[str, Any],
    ) -> TaskTypeDefinition:
        """Persist one task type and refresh the in-memory registry."""

        normalized_name = _validate_task_type_name(name)
        schema_path = self.directory / f"{normalized_name}{SCHEMA_SUFFIX}"
        policy_path = self.directory / f"{normalized_name}{POLICY_SUFFIX}"

        # Validate before touching disk so a bad payload cannot poison the registry.
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            raise SchemaLoadError(
                f"Invalid schema file {schema_path.name}: {error.message}"
            ) from error
        if not isinstance(policy, dict):
            raise SchemaLoadError("Task type policy payload must be a mapping")

        self.directory.mkdir(parents=True, exist_ok=True)
        schema_path.write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        policy_path.write_text(
            yaml.safe_dump(policy, sort_keys=True),
            encoding="utf-8",
        )

        self.load()
        return self._definitions[normalized_name]
