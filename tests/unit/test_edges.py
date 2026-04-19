from __future__ import annotations

import json
from typing import Iterator

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import EdgeModel, EdgeRelation
from agenticqueue_api.models.edge import edge_metadata_marks_superseded
from agenticqueue_api.repo import create_edge, get_edge, list_edges_by_source, list_edges_by_target


EDGE_FIXTURE = {
    "id": "00000000-0000-0000-0000-000000000201",
    "src_entity_type": "task",
    "src_id": "00000000-0000-0000-0000-000000000301",
    "dst_entity_type": "task",
    "dst_id": "00000000-0000-0000-0000-000000000302",
    "relation": "depends_on",
    "metadata": {"kind": "phase-link"},
    "created_by": None,
    "created_at": "2026-04-19T18:44:00+00:00",
}


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def make_edge_payload(**overrides: object) -> EdgeModel:
    payload = dict(EDGE_FIXTURE)
    payload.update(overrides)
    return EdgeModel.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("relation", list(EdgeRelation))
def test_edge_model_accepts_all_supported_relation_types(relation: EdgeRelation) -> None:
    payload = make_edge_payload(relation=relation.value, metadata=None)
    assert payload.relation is relation
    assert payload.metadata == {}
    assert payload.is_active is True


def test_edge_model_rejects_unknown_relation_and_invalid_entity_type() -> None:
    with pytest.raises(ValidationError):
        make_edge_payload(relation="not-a-real-edge")

    with pytest.raises(ValidationError):
        make_edge_payload(src_entity_type="   ")


def test_edge_model_normalizes_entity_types_and_requires_object_metadata() -> None:
    payload = make_edge_payload(
        src_entity_type=" task ",
        dst_entity_type=" learning ",
        metadata={"status": "active"},
    )
    assert payload.src_entity_type == "task"
    assert payload.dst_entity_type == "learning"
    assert payload.metadata == {"status": "active"}

    with pytest.raises(ValidationError):
        make_edge_payload(metadata=["not", "an", "object"])


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({}, False),
        (None, False),
        ({"superseded_at": "2026-04-19T18:50:00+00:00"}, True),
        ({"superseded_by": "AQ-61"}, True),
        ({"status": "superseded"}, True),
        ({"is_active": False}, True),
    ],
)
def test_edge_metadata_supersession_detection(
    metadata: dict[str, object] | None,
    expected: bool,
) -> None:
    payload = make_edge_payload(metadata=metadata)
    assert edge_metadata_marks_superseded(payload.metadata) is expected
    assert payload.is_active is (not expected)


def test_edge_repo_round_trip_and_directional_queries(db_session: Session) -> None:
    payload = make_edge_payload()

    created = create_edge(db_session, payload)
    loaded = get_edge(db_session, payload.id)

    assert created == payload
    assert loaded == payload
    assert list_edges_by_source(db_session, "task", payload.src_id) == [payload]
    assert list_edges_by_source(
        db_session,
        "task",
        payload.src_id,
        relation=EdgeRelation.DEPENDS_ON,
    ) == [payload]
    assert list_edges_by_target(db_session, "task", payload.dst_id) == [payload]
    assert list_edges_by_target(
        db_session,
        "task",
        payload.dst_id,
        relation=EdgeRelation.DEPENDS_ON,
    ) == [payload]
    assert (
        list_edges_by_target(
            db_session,
            "task",
            payload.dst_id,
            relation=EdgeRelation.BLOCKS,
        )
        == []
    )


def test_duplicate_edge_signature_raises_integrity_error(db_session: Session) -> None:
    create_edge(db_session, make_edge_payload())

    with pytest.raises(IntegrityError):
        create_edge(
            db_session,
            make_edge_payload(id="00000000-0000-0000-0000-000000000202"),
        )


def test_superseded_edges_are_excluded_from_active_queries(db_session: Session) -> None:
    active_edge = create_edge(db_session, make_edge_payload())
    superseded_edge = create_edge(
        db_session,
        make_edge_payload(
            id="00000000-0000-0000-0000-000000000203",
            dst_id="00000000-0000-0000-0000-000000000303",
            relation="blocks",
            metadata={"status": "superseded"},
        ),
    )

    active_ids = {
        edge.id
        for edge in list_edges_by_source(
            db_session,
            "task",
            active_edge.src_id,
        )
    }
    all_ids = {
        edge.id
        for edge in list_edges_by_source(
            db_session,
            "task",
            active_edge.src_id,
            active_only=False,
        )
    }

    assert active_edge.id in active_ids
    assert superseded_edge.id not in active_ids
    assert {active_edge.id, superseded_edge.id}.issubset(all_ids)
