from __future__ import annotations

import datetime as dt
import uuid
from typing import Iterator

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agenticqueue_api.config import (
    get_sqlalchemy_sync_database_url,
    get_sync_database_url,
)
from agenticqueue_api.memory import MemoryItemModel, MemoryItemRecord, MemoryLayer


def _deterministic_uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{label}")


def _utc(iso_value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso_value.replace("Z", "+00:00"))


def _embedding() -> list[float]:
    return [0.1] * 768


def _memory_item(
    layer: MemoryLayer,
    *,
    label: str,
    content_hash: str | None = None,
) -> MemoryItemRecord:
    return MemoryItemRecord(
        id=_deterministic_uuid(f"{layer.value}-{label}"),
        layer=layer,
        scope_id=_deterministic_uuid(f"{layer.value}-scope"),
        content_text=f"{layer.value} memory for {label}",
        content_hash=content_hash or f"{layer.value}-hash-{label}",
        embedding=None,
        source_ref=f"memory://{layer.value}/{label}",
        surface_area=[f"{layer.value}/surface", "memory/layers"],
        last_accessed_at=_utc("2026-04-20T18:00:00Z"),
        access_count=1,
        created_at=_utc("2026-04-20T17:00:00Z"),
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


def test_memory_item_model_accepts_all_layer_variants() -> None:
    for layer in MemoryLayer:
        model = MemoryItemModel.model_validate(
            {
                "id": str(_deterministic_uuid(f"{layer.value}-model")),
                "created_at": "2026-04-20T17:00:00Z",
                "layer": layer.value,
                "scope_id": str(_deterministic_uuid(f"{layer.value}-scope")),
                "content_text": f"{layer.value} content",
                "content_hash": f"{layer.value}-hash",
                "embedding": _embedding(),
                "source_ref": f"memory://{layer.value}",
                "surface_area": [f"{layer.value}/surface", "memory/layers"],
                "last_accessed_at": "2026-04-20T18:00:00Z",
                "access_count": 2,
            }
        )

        assert model.layer is layer
        assert model.embedding == _embedding()
        assert model.scope_hint


def test_memory_item_rows_round_trip_by_layer(session: Session) -> None:
    expected_ids: dict[MemoryLayer, uuid.UUID] = {}
    for layer in MemoryLayer:
        record = _memory_item(layer, label="roundtrip")
        expected_ids[layer] = record.id
        session.add(record)
    session.commit()

    for layer in MemoryLayer:
        rows = session.scalars(
            sa.select(MemoryItemRecord).where(MemoryItemRecord.layer == layer)
        ).all()

        assert len(rows) == 1
        row = rows[0]
        assert row.id == expected_ids[layer]
        assert row.layer is layer
        assert row.scope_id == _deterministic_uuid(f"{layer.value}-scope")
        assert row.surface_area == [f"{layer.value}/surface", "memory/layers"]


def test_memory_item_uniqueness_constraint_rejects_duplicate_layer_scope_hash(
    session: Session,
) -> None:
    session.add(_memory_item(MemoryLayer.PROJECT, label="unique", content_hash="dupe"))
    session.commit()

    session.add(
        _memory_item(
            MemoryLayer.PROJECT,
            label="duplicate",
            content_hash="dupe",
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()

    session.rollback()


def test_memory_item_surface_area_uses_a_gin_index() -> None:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexname, indexdef "
                "FROM pg_indexes "
                "WHERE schemaname = 'agenticqueue' AND tablename = 'memory_item'"
            )
            indexes = {row[0]: row[1] for row in cursor.fetchall()}

    assert "ix_memory_item_surface_area_gin" in indexes
    assert "USING gin" in indexes["ix_memory_item_surface_area_gin"]
