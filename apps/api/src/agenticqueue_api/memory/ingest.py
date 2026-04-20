"""Ingest helpers for file-backed memory sync runs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
import logging
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from agenticqueue_api.memory.layers import MemoryItemRecord, MemoryLayer

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MemoryIngestItem:
    """One source-backed memory row emitted by a sync walk."""

    source_ref: str
    content_text: str
    content_hash: str
    surface_area: tuple[str, ...] = ()
    embedding: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        source_ref = self.source_ref.strip()
        if not source_ref:
            raise ValueError("source_ref must not be empty")
        object.__setattr__(self, "source_ref", source_ref)

        content_text = self.content_text.strip()
        if not content_text:
            raise ValueError("content_text must not be empty")
        object.__setattr__(self, "content_text", content_text)

        content_hash = self.content_hash.strip().lower()
        if not content_hash:
            raise ValueError("content_hash must not be empty")
        object.__setattr__(self, "content_hash", content_hash)

        surface_area = tuple(
            value.strip() for value in self.surface_area if value.strip()
        )
        object.__setattr__(self, "surface_area", surface_area)

        if self.embedding is not None:
            object.__setattr__(
                self,
                "embedding",
                tuple(float(value) for value in self.embedding),
            )


@dataclass(frozen=True, slots=True)
class MemoryIngestResult:
    """Outcome for one ingest run."""

    upserted: int
    pruned: int
    full_sync: bool
    partial: bool


class MemoryIngestService:
    """Persist source-backed memory rows and prune stale full-sync rows."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def ingest(
        self,
        *,
        layer: MemoryLayer,
        scope_id: uuid.UUID,
        items: Sequence[MemoryIngestItem],
        full_sync: bool = False,
        paths: Sequence[str] | None = None,
    ) -> MemoryIngestResult:
        normalized_paths = tuple(
            str(path).strip() for path in (paths or ()) if str(path).strip()
        )
        is_partial = bool(normalized_paths)
        seen_hashes_by_source: dict[str, set[str]] = defaultdict(set)

        existing_by_hash = self._existing_rows(
            layer=layer,
            scope_id=scope_id,
            content_hashes={item.content_hash for item in items},
        )

        upserted = 0
        for item in items:
            seen_hashes_by_source[item.source_ref].add(item.content_hash)
            row = existing_by_hash.get(item.content_hash)
            if row is None:
                row = MemoryItemRecord(
                    layer=layer,
                    scope_id=scope_id,
                    content_text=item.content_text,
                    content_hash=item.content_hash,
                    embedding=self._embedding_list(item),
                    source_ref=item.source_ref,
                    surface_area=list(item.surface_area),
                )
                self._session.add(row)
                existing_by_hash[item.content_hash] = row
            else:
                row.content_text = item.content_text
                row.embedding = self._embedding_list(item)
                row.source_ref = item.source_ref
                row.surface_area = list(item.surface_area)
            upserted += 1

        pruned = 0
        if full_sync and not is_partial:
            pruned = self._prune_stale_rows(
                layer=layer,
                scope_id=scope_id,
                seen_hashes_by_source=seen_hashes_by_source,
            )

        self._session.flush()
        return MemoryIngestResult(
            upserted=upserted,
            pruned=pruned,
            full_sync=full_sync,
            partial=is_partial,
        )

    def _existing_rows(
        self,
        *,
        layer: MemoryLayer,
        scope_id: uuid.UUID,
        content_hashes: set[str],
    ) -> dict[str, MemoryItemRecord]:
        if not content_hashes:
            return {}

        rows = self._session.scalars(
            sa.select(MemoryItemRecord).where(
                MemoryItemRecord.layer == layer,
                MemoryItemRecord.scope_id == scope_id,
                MemoryItemRecord.content_hash.in_(sorted(content_hashes)),
            )
        )
        return {row.content_hash: row for row in rows}

    def _prune_stale_rows(
        self,
        *,
        layer: MemoryLayer,
        scope_id: uuid.UUID,
        seen_hashes_by_source: dict[str, set[str]],
    ) -> int:
        stale_ids: list[uuid.UUID] = []
        rows = self._session.scalars(
            sa.select(MemoryItemRecord).where(
                MemoryItemRecord.layer == layer,
                MemoryItemRecord.scope_id == scope_id,
                MemoryItemRecord.source_ref.is_not(None),
            )
        )
        for row in rows:
            if row.source_ref is None:
                continue
            seen_hashes = seen_hashes_by_source.get(row.source_ref)
            if not seen_hashes or row.content_hash not in seen_hashes:
                stale_ids.append(row.id)

        if stale_ids:
            self._session.execute(
                sa.delete(MemoryItemRecord).where(MemoryItemRecord.id.in_(stale_ids))
            )

        logger.info(
            "memory.ingest.prune",
            extra={
                "pruned": len(stale_ids),
                "source": "full_sync",
                "layer": layer.value,
                "scope_id": str(scope_id),
            },
        )
        return len(stale_ids)

    @staticmethod
    def _embedding_list(item: MemoryIngestItem) -> list[float] | None:
        if item.embedding is None:
            return None
        return list(item.embedding)


__all__ = [
    "MemoryIngestItem",
    "MemoryIngestResult",
    "MemoryIngestService",
]
