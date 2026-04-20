"""Filesystem-backed memory sync helpers."""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
from pathlib import Path
import uuid

from sqlalchemy.orm import Session

from agenticqueue_api.memory.ingest import (
    MemoryIngestItem,
    MemoryIngestResult,
    MemoryIngestService,
)
from agenticqueue_api.memory.layers import MemoryLayer


class MemorySyncService:
    """Read source files and persist them through the ingest service."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def sync(
        self,
        *,
        layer: MemoryLayer,
        scope_id: uuid.UUID,
        paths: Sequence[str],
        full_sync: bool = False,
    ) -> MemoryIngestResult:
        source_paths = self._resolve_source_paths(paths)
        items = [self._item_from_path(path) for path in source_paths]

        # AQ-83 reserves `paths=[...]` for partial walks that must not prune.
        # AQ-86 treats `full_sync=True` as "these roots are authoritative", so
        # the ingest layer receives `paths=None` to enable prune semantics.
        partial_paths = (
            None if full_sync else [path.as_posix() for path in source_paths]
        )

        return MemoryIngestService(self._session).ingest(
            layer=layer,
            scope_id=scope_id,
            items=items,
            full_sync=full_sync,
            paths=partial_paths,
        )

    def _resolve_source_paths(self, paths: Sequence[str]) -> list[Path]:
        if not paths:
            raise ValueError("paths must contain at least one file or directory")

        resolved: list[Path] = []
        seen: set[str] = set()
        for raw_path in paths:
            normalized = str(raw_path).strip()
            if not normalized:
                continue

            root = Path(normalized).expanduser().resolve()
            if not root.exists():
                raise FileNotFoundError(f"path not found: {root}")

            if root.is_dir():
                for candidate in sorted(
                    (path.resolve() for path in root.rglob("*") if path.is_file()),
                    key=lambda path: path.as_posix(),
                ):
                    key = candidate.as_posix()
                    if key not in seen:
                        seen.add(key)
                        resolved.append(candidate)
                continue

            key = root.as_posix()
            if key not in seen:
                seen.add(key)
                resolved.append(root)

        if not resolved:
            raise ValueError("paths did not resolve to any files")
        return resolved

    def _item_from_path(self, path: Path) -> MemoryIngestItem:
        try:
            content_text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"path is not UTF-8 text: {path}") from error

        source_ref = path.as_posix()
        content_hash = hashlib.sha256(
            f"{source_ref}\0{content_text}".encode("utf-8")
        ).hexdigest()
        tail_parts = [part for part in path.parts[-4:] if part]
        surface_area = tuple(dict.fromkeys([source_ref, *tail_parts]))
        return MemoryIngestItem(
            source_ref=source_ref,
            content_text=content_text,
            content_hash=content_hash,
            surface_area=surface_area,
        )


__all__ = ["MemorySyncService"]
