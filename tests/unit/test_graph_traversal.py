from __future__ import annotations

import json
import uuid
from typing import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import EdgeModel, EdgeRelation
from agenticqueue_api.repo import (
    CycleError,
    ancestors,
    create_edge,
    descendants,
    downstream_of_decision,
    neighbors,
    shortest_path,
)

TASK_A = uuid.UUID("00000000-0000-0000-0000-000000000401")
TASK_B = uuid.UUID("00000000-0000-0000-0000-000000000402")
TASK_C = uuid.UUID("00000000-0000-0000-0000-000000000403")
TASK_D = uuid.UUID("00000000-0000-0000-0000-000000000404")
DECISION_X = uuid.UUID("00000000-0000-0000-0000-000000000405")
ARTIFACT_Y = uuid.UUID("00000000-0000-0000-0000-000000000406")
RUN_Z = uuid.UUID("00000000-0000-0000-0000-000000000407")
LEARNING_Q = uuid.UUID("00000000-0000-0000-0000-000000000408")


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


def make_edge_payload(
    edge_suffix: int,
    *,
    src_entity_type: str,
    src_id: uuid.UUID,
    dst_entity_type: str,
    dst_id: uuid.UUID,
    relation: EdgeRelation | str,
    metadata: dict[str, object] | None = None,
) -> EdgeModel:
    payload = {
        "id": f"00000000-0000-0000-0000-{edge_suffix:012d}",
        "src_entity_type": src_entity_type,
        "src_id": str(src_id),
        "dst_entity_type": dst_entity_type,
        "dst_id": str(dst_id),
        "relation": relation.value if isinstance(relation, EdgeRelation) else relation,
        "metadata": metadata or {},
        "created_by": None,
        "created_at": "2026-04-20T00:00:00+00:00",
    }
    return EdgeModel.model_validate_json(json.dumps(payload))


def seed_fixture_graph(db_session: Session) -> None:
    fixture_edges = [
        make_edge_payload(
            1,
            src_entity_type="decision",
            src_id=DECISION_X,
            dst_entity_type="task",
            dst_id=TASK_A,
            relation=EdgeRelation.TRIGGERED,
        ),
        make_edge_payload(
            2,
            src_entity_type="task",
            src_id=TASK_A,
            dst_entity_type="task",
            dst_id=TASK_B,
            relation=EdgeRelation.DEPENDS_ON,
        ),
        make_edge_payload(
            3,
            src_entity_type="task",
            src_id=TASK_B,
            dst_entity_type="task",
            dst_id=TASK_C,
            relation=EdgeRelation.DEPENDS_ON,
        ),
        make_edge_payload(
            4,
            src_entity_type="task",
            src_id=TASK_C,
            dst_entity_type="task",
            dst_id=TASK_D,
            relation=EdgeRelation.DEPENDS_ON,
        ),
        make_edge_payload(
            5,
            src_entity_type="task",
            src_id=TASK_A,
            dst_entity_type="artifact",
            dst_id=ARTIFACT_Y,
            relation=EdgeRelation.PRODUCED,
        ),
        make_edge_payload(
            6,
            src_entity_type="task",
            src_id=TASK_A,
            dst_entity_type="run",
            dst_id=RUN_Z,
            relation=EdgeRelation.TRIGGERED,
        ),
        make_edge_payload(
            7,
            src_entity_type="task",
            src_id=TASK_B,
            dst_entity_type="learning",
            dst_id=LEARNING_Q,
            relation=EdgeRelation.INFORMED_BY,
        ),
    ]

    for payload in fixture_edges:
        create_edge(db_session, payload)


def test_neighbors_return_depth_1_and_depth_n_results(db_session: Session) -> None:
    seed_fixture_graph(db_session)

    depth_1 = neighbors(db_session, "task", TASK_A, depth=1)
    depth_2 = neighbors(db_session, "task", TASK_A, depth=2)

    assert [(hit.entity_type, hit.entity_id, hit.depth) for hit in depth_1] == [
        ("artifact", ARTIFACT_Y, 1),
        ("decision", DECISION_X, 1),
        ("run", RUN_Z, 1),
        ("task", TASK_B, 1),
    ]
    assert [(hit.entity_type, hit.entity_id, hit.depth) for hit in depth_2] == [
        ("artifact", ARTIFACT_Y, 1),
        ("decision", DECISION_X, 1),
        ("run", RUN_Z, 1),
        ("task", TASK_B, 1),
        ("learning", LEARNING_Q, 2),
        ("task", TASK_C, 2),
    ]


def test_ancestors_and_descendants_use_bounded_recursive_ctes(
    db_session: Session,
) -> None:
    seed_fixture_graph(db_session)

    descendant_hits = descendants(
        db_session,
        "task",
        TASK_A,
        edge_types=(EdgeRelation.DEPENDS_ON,),
    )
    ancestor_hits = ancestors(
        db_session,
        "task",
        TASK_D,
        edge_types=(EdgeRelation.DEPENDS_ON,),
    )

    assert [(hit.entity_id, hit.depth) for hit in descendant_hits] == [
        (TASK_B, 1),
        (TASK_C, 2),
        (TASK_D, 3),
    ]
    assert [(hit.entity_id, hit.depth) for hit in ancestor_hits] == [
        (TASK_C, 1),
        (TASK_B, 2),
        (TASK_A, 3),
    ]


def test_shortest_path_returns_directed_path_and_no_path_case(
    db_session: Session,
) -> None:
    seed_fixture_graph(db_session)

    path = shortest_path(
        db_session,
        "decision",
        DECISION_X,
        "task",
        TASK_D,
    )

    assert path is not None
    assert [node.entity_id for node in path.nodes] == [
        DECISION_X,
        TASK_A,
        TASK_B,
        TASK_C,
        TASK_D,
    ]
    assert path.relations == [
        EdgeRelation.TRIGGERED,
        EdgeRelation.DEPENDS_ON,
        EdgeRelation.DEPENDS_ON,
        EdgeRelation.DEPENDS_ON,
    ]
    assert (
        shortest_path(
            db_session,
            "artifact",
            ARTIFACT_Y,
            "learning",
            LEARNING_Q,
        )
        is None
    )


def test_downstream_of_decision_filters_to_task_artifact_and_run(
    db_session: Session,
) -> None:
    seed_fixture_graph(db_session)

    hits = downstream_of_decision(db_session, DECISION_X)
    hit_keys = {(hit.entity_type, hit.entity_id) for hit in hits}

    assert hit_keys == {
        ("task", TASK_A),
        ("task", TASK_B),
        ("task", TASK_C),
        ("task", TASK_D),
        ("artifact", ARTIFACT_Y),
        ("run", RUN_Z),
    }


def test_shortest_path_respects_depth_bounds_and_skips_longer_revisits(
    db_session: Session,
) -> None:
    task_e = uuid.UUID("00000000-0000-0000-0000-000000000409")
    task_f = uuid.UUID("00000000-0000-0000-0000-000000000410")

    for payload in [
        make_edge_payload(
            81,
            src_entity_type="task",
            src_id=TASK_A,
            dst_entity_type="task",
            dst_id=TASK_B,
            relation=EdgeRelation.DEPENDS_ON,
        ),
        make_edge_payload(
            82,
            src_entity_type="task",
            src_id=TASK_A,
            dst_entity_type="task",
            dst_id=TASK_C,
            relation=EdgeRelation.DEPENDS_ON,
        ),
        make_edge_payload(
            83,
            src_entity_type="task",
            src_id=TASK_B,
            dst_entity_type="task",
            dst_id=TASK_D,
            relation=EdgeRelation.DEPENDS_ON,
        ),
        make_edge_payload(
            84,
            src_entity_type="task",
            src_id=TASK_C,
            dst_entity_type="task",
            dst_id=TASK_D,
            relation=EdgeRelation.DEPENDS_ON,
        ),
        make_edge_payload(
            85,
            src_entity_type="task",
            src_id=TASK_D,
            dst_entity_type="task",
            dst_id=task_e,
            relation=EdgeRelation.DEPENDS_ON,
        ),
    ]:
        create_edge(db_session, payload)

    assert (
        shortest_path(
            db_session,
            "task",
            TASK_A,
            "task",
            task_e,
            edge_types=(EdgeRelation.DEPENDS_ON,),
            max_depth=1,
        )
        is None
    )
    assert (
        shortest_path(
            db_session,
            "task",
            TASK_A,
            "task",
            task_f,
            edge_types=(EdgeRelation.DEPENDS_ON,),
        )
        is None
    )


def test_create_edge_rejects_cycles_and_self_reference(db_session: Session) -> None:
    create_edge(
        db_session,
        make_edge_payload(
            101,
            src_entity_type="task",
            src_id=TASK_A,
            dst_entity_type="task",
            dst_id=TASK_B,
            relation=EdgeRelation.DEPENDS_ON,
        ),
    )
    create_edge(
        db_session,
        make_edge_payload(
            102,
            src_entity_type="task",
            src_id=TASK_B,
            dst_entity_type="task",
            dst_id=TASK_C,
            relation=EdgeRelation.DEPENDS_ON,
        ),
    )

    with pytest.raises(CycleError):
        create_edge(
            db_session,
            make_edge_payload(
                103,
                src_entity_type="task",
                src_id=TASK_C,
                dst_entity_type="task",
                dst_id=TASK_A,
                relation=EdgeRelation.DEPENDS_ON,
            ),
        )

    with pytest.raises(CycleError):
        create_edge(
            db_session,
            make_edge_payload(
                104,
                src_entity_type="task",
                src_id=TASK_D,
                dst_entity_type="task",
                dst_id=TASK_D,
                relation=EdgeRelation.DEPENDS_ON,
            ),
        )


def test_descendants_truncate_at_default_max_depth(db_session: Session) -> None:
    root_id = uuid.UUID("00000000-0000-0000-0000-000000000500")
    chain_ids = [
        uuid.UUID(f"00000000-0000-0000-0000-{index:012d}") for index in range(501, 603)
    ]

    current_id = root_id
    for edge_suffix, next_id in enumerate(chain_ids, start=201):
        create_edge(
            db_session,
            make_edge_payload(
                edge_suffix,
                src_entity_type="task",
                src_id=current_id,
                dst_entity_type="task",
                dst_id=next_id,
                relation=EdgeRelation.DEPENDS_ON,
            ),
        )
        current_id = next_id

    hits = descendants(
        db_session,
        "task",
        root_id,
        edge_types=(EdgeRelation.DEPENDS_ON,),
    )

    assert len(hits) == 100
    assert hits[-1].entity_id == chain_ids[99]
    assert chain_ids[100] not in {hit.entity_id for hit in hits}


def test_graph_helpers_validate_depth_and_entity_type(db_session: Session) -> None:
    with pytest.raises(ValueError):
        descendants(db_session, "task", TASK_A, max_depth=0)

    with pytest.raises(ValueError):
        neighbors(db_session, "   ", TASK_A)
