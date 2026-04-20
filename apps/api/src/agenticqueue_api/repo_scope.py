"""Resolve task-declared repo scope into a concrete allowlist."""

from __future__ import annotations

import math
from pathlib import Path, PurePosixPath
from typing import Iterable

from pydantic import Field

from agenticqueue_api.models.shared import SchemaModel

ESTIMATED_BYTES_PER_TOKEN = 4
GLOB_CHARS = ("*", "?", "[")


class ResolvedRepoScope(SchemaModel):
    """Concrete repo-relative paths plus a coarse token estimate."""

    file_scope: list[str] = Field(default_factory=list)
    estimated_token_count: int = 0


def _normalize_scope_entry(entry: str) -> str:
    normalized = entry.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.rstrip("/")


def _validate_scope_entry(entry: str) -> str:
    normalized = _normalize_scope_entry(entry)
    if not normalized:
        raise ValueError("file_scope entries must not be empty")
    scope_path = PurePosixPath(normalized)
    has_windows_drive = len(scope_path.parts) > 0 and scope_path.parts[0].endswith(":")
    if scope_path.is_absolute() or has_windows_drive:
        raise ValueError(f"file_scope entry must be repo-relative: {entry}")
    if any(part == ".." for part in scope_path.parts):
        raise ValueError(f"file_scope entry must stay inside the repo: {entry}")
    return normalized


def _contains_glob(entry: str) -> bool:
    return any(token in entry for token in GLOB_CHARS)


def _sorted_files(paths: Iterable[Path]) -> list[Path]:
    return sorted(
        (path for path in paths if path.is_file()),
        key=lambda path: path.as_posix(),
    )


def _expand_scope_entry(repo_root: Path, entry: str) -> list[Path]:
    if entry == "**" or entry.endswith("/**"):
        subtree_root = entry[:-3].rstrip("/")
        target = repo_root if not subtree_root else (repo_root / Path(subtree_root)).resolve()
        try:
            target.relative_to(repo_root)
        except ValueError as error:
            raise ValueError(
                f"file_scope entry must stay inside the repo: {entry}"
            ) from error
        if target.is_dir():
            matches = _sorted_files(target.rglob("*"))
            if matches:
                return matches
        raise ValueError(f"file_scope entry did not match any files: {entry}")

    if _contains_glob(entry):
        matches = _sorted_files(repo_root.glob(entry))
        if matches:
            return matches
        raise ValueError(f"file_scope entry did not match any files: {entry}")

    target = (repo_root / Path(entry)).resolve()
    try:
        target.relative_to(repo_root)
    except ValueError as error:
        raise ValueError(
            f"file_scope entry must stay inside the repo: {entry}"
        ) from error

    if target.is_file():
        return [target]
    if target.is_dir():
        matches = _sorted_files(target.rglob("*"))
        if matches:
            return matches
        raise ValueError(f"file_scope directory is empty: {entry}")
    raise ValueError(f"file_scope entry did not match any files: {entry}")


def resolve_repo_scope(
    repo_root: Path,
    file_scope: list[str],
    *,
    max_files: int,
) -> ResolvedRepoScope:
    """Resolve exact paths, directory subtrees, and globs against one repo root."""

    if max_files < 1:
        raise ValueError("max_files must be at least 1")

    root = repo_root.resolve()
    if not root.is_dir():
        raise ValueError(f"repo_root must be an existing directory: {repo_root}")

    if not file_scope:
        return ResolvedRepoScope()

    resolved_paths: list[str] = []
    seen: set[str] = set()
    total_bytes = 0

    for raw_entry in file_scope:
        entry = _validate_scope_entry(raw_entry)
        for match in _expand_scope_entry(root, entry):
            relative = match.relative_to(root).as_posix()
            if relative in seen:
                continue
            resolved_paths.append(relative)
            seen.add(relative)
            total_bytes += match.stat().st_size
            if len(resolved_paths) > max_files:
                raise ValueError(
                    f"resolved repo scope exceeds max_files={max_files}: {len(resolved_paths)}"
                )

    return ResolvedRepoScope(
        file_scope=resolved_paths,
        estimated_token_count=math.ceil(total_bytes / ESTIMATED_BYTES_PER_TOKEN),
    )


__all__ = ["ResolvedRepoScope", "resolve_repo_scope"]
