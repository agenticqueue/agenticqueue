from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.memory import (
    MemoryIngestItem,
    MemoryIngestResult,
    MemoryIngestService,
    MemoryItemRecord,
    MemoryLayer,
)


def _uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{label}")


def _utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _memory_row(
    *,
    label: str,
    scope_id: uuid.UUID,
    source_ref: str | None,
    content_hash: str,
) -> MemoryItemRecord:
    return MemoryItemRecord(
        id=_uuid(label),
        layer=MemoryLayer.PROJECT,
        scope_id=scope_id,
        content_text=f"memory row for {label}",
        content_hash=content_hash,
        embedding=None,
        source_ref=source_ref,
        surface_area=["memory/ingest"],
        created_at=_utc("2026-04-20T18:00:00Z"),
    )


def _truncate_memory_items(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text("TRUNCATE TABLE agenticqueue.memory_item RESTART IDENTITY CASCADE")
        )


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    _truncate_memory_items(engine)
    connection = engine.connect()
    transaction = connection.begin()
    db_session = Session(bind=connection, expire_on_commit=False)
    try:
        yield db_session
    finally:
        db_session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def test_ingest_full_sync_prunes_deleted_and_stale_source_rows(
    session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    scope_id = _uuid("full-sync-scope")
    session.add_all(
        [
            _memory_row(
                label="updated-old",
                scope_id=scope_id,
                source_ref="docs/keep.md",
                content_hash="keep-old",
            ),
            _memory_row(
                label="deleted-file",
                scope_id=scope_id,
                source_ref="docs/deleted.md",
                content_hash="deleted-old",
            ),
            _memory_row(
                label="manual-memory",
                scope_id=scope_id,
                source_ref=None,
                content_hash="manual-hash",
            ),
        ]
    )
    session.flush()

    caplog.set_level(logging.INFO, logger="agenticqueue_api.memory.ingest")
    result = MemoryIngestService(session).ingest(
        layer=MemoryLayer.PROJECT,
        scope_id=scope_id,
        items=[
            MemoryIngestItem(
                source_ref="docs/keep.md",
                content_text="updated keep content",
                content_hash="keep-new",
                surface_area=("memory/ingest", "docs/keep.md"),
            )
        ],
        full_sync=True,
    )

    remaining_rows = session.scalars(
        sa.select(MemoryItemRecord)
        .where(
            MemoryItemRecord.layer == MemoryLayer.PROJECT,
            MemoryItemRecord.scope_id == scope_id,
        )
        .order_by(MemoryItemRecord.content_hash.asc())
    ).all()

    assert [row.content_hash for row in remaining_rows] == ["keep-new", "manual-hash"]
    assert [row.source_ref for row in remaining_rows] == ["docs/keep.md", None]
    assert result == MemoryIngestResult(
        upserted=1,
        pruned=2,
        full_sync=True,
        partial=False,
    )

    prune_record = next(
        record for record in caplog.records if record.msg == "memory.ingest.prune"
    )
    assert prune_record.pruned == 2
    assert prune_record.source == "full_sync"
    assert prune_record.layer == MemoryLayer.PROJECT.value
    assert prune_record.scope_id == str(scope_id)


def test_ingest_partial_walk_never_prunes_existing_rows(session: Session) -> None:
    scope_id = _uuid("partial-sync-scope")
    session.add_all(
        [
            _memory_row(
                label="partial-updated-old",
                scope_id=scope_id,
                source_ref="docs/keep.md",
                content_hash="keep-old",
            ),
            _memory_row(
                label="partial-deleted-file",
                scope_id=scope_id,
                source_ref="docs/deleted.md",
                content_hash="deleted-old",
            ),
        ]
    )
    session.flush()

    result = MemoryIngestService(session).ingest(
        layer=MemoryLayer.PROJECT,
        scope_id=scope_id,
        items=[
            MemoryIngestItem(
                source_ref="docs/keep.md",
                content_text="updated keep content",
                content_hash="keep-new",
                surface_area=("memory/ingest", "docs/keep.md"),
            )
        ],
        full_sync=True,
        paths=["docs/keep.md"],
    )

    remaining_hashes = session.scalars(
        sa.select(MemoryItemRecord.content_hash)
        .where(
            MemoryItemRecord.layer == MemoryLayer.PROJECT,
            MemoryItemRecord.scope_id == scope_id,
        )
        .order_by(MemoryItemRecord.content_hash.asc())
    ).all()

    assert remaining_hashes == ["deleted-old", "keep-new", "keep-old"]
    assert result.pruned == 0
    assert result.partial is True
