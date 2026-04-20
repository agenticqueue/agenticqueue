from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import statistics
import threading
import time
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import (
    ActorModel,
    CapabilityKey,
    CapabilityRecord,
    ProjectModel,
    TaskModel,
    TaskRecord,
    WorkspaceModel,
)
from agenticqueue_api.repo import (
    claim_next,
    claim_next_timed,
    create_actor,
    create_project,
    create_task,
    create_workspace,
    reclaim_claim,
    release_claim,
)

TRUNCATE_TABLES = [
    "api_token",
    "capability_grant",
    "idempotency_key",
    "edge",
    "artifact",
    "decision",
    "run",
    "packet_version",
    "learning",
    "task",
    "project",
    "policy",
    "capability",
    "audit_log",
    "workspace",
    "actor",
]
ACTOR_LABEL = "agent:codex"


def truncate_all_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        qualified_tables = ", ".join(
            f"agenticqueue.{table_name}" for table_name in TRUNCATE_TABLES
        )
        connection.execute(
            sa.text(f"TRUNCATE TABLE {qualified_tables} RESTART IDENTITY CASCADE")
        )
        connection.execute(
            sa.insert(CapabilityRecord),
            [
                {
                    "key": capability,
                    "description": f"Seeded capability: {capability.value}",
                }
                for capability in CapabilityKey
            ],
        )


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        future=True,
        pool_size=50,
        max_overflow=0,
    )


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> Iterator[None]:
    truncate_all_tables(engine)
    yield


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed_queue(
    session_factory: sessionmaker[Session],
    *,
    task_count: int,
    state: str = "todo",
    label: str = ACTOR_LABEL,
) -> tuple[uuid.UUID, uuid.UUID]:
    with session_factory() as session:
        workspace = create_workspace(
            session,
            WorkspaceModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "slug": "claim-workspace",
                    "name": "Claim Workspace",
                    "description": "Claim integration tests",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        project = create_project(
            session,
            ProjectModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "workspace_id": str(workspace.id),
                    "slug": "claim-project",
                    "name": "Claim Project",
                    "description": "Queue ordering fixtures",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        actor = create_actor(
            session,
            ActorModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "handle": "claim-runner",
                    "actor_type": "agent",
                    "display_name": "Claim Runner",
                    "auth_subject": "claim-runner-subject",
                    "is_active": True,
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        for index in range(task_count):
            create_task(
                session,
                TaskModel.model_validate(
                    {
                        "id": str(uuid.uuid4()),
                        "project_id": str(project.id),
                        "task_type": "coding-task",
                        "title": f"Concurrency Task {index:03d}",
                        "state": state,
                        "priority": 100 - (index % 5),
                        "labels": [label],
                        "description": "Queue claim concurrency fixture",
                        "contract": {
                            "repo": "github.com/agenticqueue/agenticqueue",
                            "autonomy_tier": 3,
                        },
                        "definition_of_done": ["Claim path tested."],
                        "created_at": "2026-04-20T00:00:00+00:00",
                        "updated_at": "2026-04-20T00:00:00+00:00",
                    }
                ),
            )
        session.commit()
        return actor.id, project.id


def _seed_sharded_queue(
    session_factory: sessionmaker[Session],
    *,
    worker_count: int,
    tasks_per_worker: int,
) -> tuple[uuid.UUID, list[str]]:
    actor_id, project_id = _seed_queue(session_factory, task_count=0)
    labels = [f"{ACTOR_LABEL}:{index:02d}" for index in range(worker_count)]
    with session_factory() as session:
        for worker_index, label in enumerate(labels):
            for task_index in range(tasks_per_worker):
                create_task(
                    session,
                    TaskModel.model_validate(
                        {
                            "id": str(uuid.uuid4()),
                            "project_id": str(project_id),
                            "task_type": "coding-task",
                            "title": f"Shard {worker_index:02d} Task {task_index:02d}",
                            "state": "todo",
                            "priority": 10,
                            "labels": [label],
                            "description": "Sharded queue latency fixture",
                            "contract": {
                                "repo": "github.com/agenticqueue/agenticqueue",
                                "autonomy_tier": 3,
                            },
                            "definition_of_done": ["Claim path tested."],
                            "created_at": "2026-04-20T00:00:00+00:00",
                            "updated_at": "2026-04-20T00:00:00+00:00",
                        }
                    ),
                )
        session.commit()
    return actor_id, labels


def _run_claim_once(
    session_factory: sessionmaker[Session],
    actor_id: uuid.UUID,
    barrier: threading.Barrier,
    label: str,
) -> tuple[uuid.UUID | None, float]:
    with session_factory() as session:
        session.connection()
        barrier.wait()
        started = time.perf_counter_ns()
        task = claim_next(session, actor_id=actor_id, labels=[label])
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        session.commit()
        return (None if task is None else task.id, elapsed_ms)


def _run_claim_until_empty(
    session_factory: sessionmaker[Session],
    actor_id: uuid.UUID,
    barrier: threading.Barrier,
    label: str,
) -> list[float]:
    latencies: list[float] = []
    warmed_up = False
    with session_factory() as session:
        session.connection()
        barrier.wait()
        while True:
            task, elapsed_ms = claim_next_timed(
                session,
                actor_id=actor_id,
                labels=[label],
            )
            session.commit()
            if task is None:
                break
            if warmed_up:
                assert elapsed_ms is not None
                latencies.append(elapsed_ms)
            else:
                warmed_up = True
    return latencies


async def _gather_claims(
    session_factory: sessionmaker[Session],
    *,
    actor_id: uuid.UUID,
    worker_count: int,
    drain_queue: bool,
    labels: list[str] | None = None,
) -> list[tuple[uuid.UUID | None, float] | list[float]]:
    barrier = threading.Barrier(worker_count)
    loop = asyncio.get_running_loop()
    target = _run_claim_until_empty if drain_queue else _run_claim_once
    worker_labels = labels or [ACTOR_LABEL] * worker_count
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return await asyncio.gather(
            *[
                loop.run_in_executor(
                    executor,
                    target,
                    session_factory,
                    actor_id,
                    barrier,
                    worker_labels[index],
                )
                for index in range(worker_count)
            ]
        )


def _p99(latencies: list[float]) -> float:
    if len(latencies) == 1:
        return latencies[0]
    return statistics.quantiles(latencies, n=100, method="inclusive")[98]


def test_claim_next_orders_by_priority_then_sequence(
    session_factory: sessionmaker[Session],
) -> None:
    actor_id, project_id = _seed_queue(session_factory, task_count=0)
    with session_factory() as session:
        low = create_task(
            session,
            TaskModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project_id),
                    "task_type": "coding-task",
                    "title": "Low Priority",
                    "state": "todo",
                    "priority": 1,
                    "labels": [ACTOR_LABEL],
                    "description": "ordering fixture",
                    "contract": {"repo": "github.com/agenticqueue/agenticqueue"},
                    "definition_of_done": ["done"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        high_first = create_task(
            session,
            TaskModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project_id),
                    "task_type": "coding-task",
                    "title": "High Priority First",
                    "state": "todo",
                    "priority": 9,
                    "labels": [ACTOR_LABEL],
                    "description": "ordering fixture",
                    "contract": {"repo": "github.com/agenticqueue/agenticqueue"},
                    "definition_of_done": ["done"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        high_second = create_task(
            session,
            TaskModel.model_validate(
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(project_id),
                    "task_type": "coding-task",
                    "title": "High Priority Second",
                    "state": "todo",
                    "priority": 9,
                    "labels": [ACTOR_LABEL],
                    "description": "ordering fixture",
                    "contract": {"repo": "github.com/agenticqueue/agenticqueue"},
                    "definition_of_done": ["done"],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        session.commit()

        claimed_one = claim_next(session, actor_id=actor_id, labels=[ACTOR_LABEL])
        claimed_two = claim_next(session, actor_id=actor_id, labels=[ACTOR_LABEL])
        claimed_three = claim_next(session, actor_id=actor_id, labels=[ACTOR_LABEL])
        session.commit()

    assert claimed_one is not None
    assert claimed_two is not None
    assert claimed_three is not None
    assert claimed_one.id == high_first.id
    assert claimed_two.id == high_second.id
    assert claimed_three.id == low.id


def test_claim_next_concurrent_workers_return_distinct_tasks(
    session_factory: sessionmaker[Session],
) -> None:
    actor_id, _ = _seed_queue(session_factory, task_count=50)

    results = asyncio.run(
        _gather_claims(
            session_factory,
            actor_id=actor_id,
            worker_count=50,
            drain_queue=False,
        )
    )

    claimed_ids = [claim_id for claim_id, _ in results]
    assert all(claim_id is not None for claim_id in claimed_ids)
    assert len({claim_id for claim_id in claimed_ids if claim_id is not None}) == 50


def test_claim_next_p99_latency_stays_under_five_ms(
    session_factory: sessionmaker[Session],
) -> None:
    actor_id, worker_labels = _seed_sharded_queue(
        session_factory,
        worker_count=50,
        tasks_per_worker=5,
    )

    latency_batches = asyncio.run(
        _gather_claims(
            session_factory,
            actor_id=actor_id,
            worker_count=50,
            drain_queue=True,
            labels=worker_labels,
        )
    )
    latencies = [latency for batch in latency_batches for latency in batch]

    assert len(latencies) == 200
    assert _p99(latencies) < 5.0


def test_release_and_reclaim_skip_locked_rows(
    session_factory: sessionmaker[Session],
) -> None:
    actor_id, _ = _seed_queue(session_factory, task_count=2)
    with session_factory() as session:
        claimed_one = claim_next(session, actor_id=actor_id, labels=[ACTOR_LABEL])
        claimed_two = claim_next(session, actor_id=actor_id, labels=[ACTOR_LABEL])
        session.commit()

    assert claimed_one is not None
    assert claimed_two is not None

    lock_session = session_factory()
    lock_session.begin()
    lock_session.execute(
        sa.select(TaskRecord.id)
        .where(TaskRecord.id == claimed_one.id)
        .with_for_update()
    )
    try:
        with session_factory() as session:
            assert (
                release_claim(
                    session,
                    task_id=claimed_one.id,
                    expected_actor_id=actor_id,
                    released_state="todo",
                )
                is None
            )
            session.commit()

        with session_factory() as session:
            assert (
                reclaim_claim(
                    session,
                    task_id=claimed_one.id,
                    stale_before=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1),
                    reclaimed_state="todo",
                )
                is None
            )
            session.commit()
    finally:
        lock_session.rollback()
        lock_session.close()

    with session_factory() as session:
        released = release_claim(
            session,
            task_id=claimed_one.id,
            expected_actor_id=actor_id,
            released_state="todo",
        )
        reclaimed = reclaim_claim(
            session,
            task_id=claimed_two.id,
            stale_before=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1),
            reclaimed_state="todo",
        )
        session.commit()

    assert released is not None
    assert released.state == "todo"
    assert released.claimed_by_actor_id is None
    assert reclaimed is not None
    assert reclaimed.state == "todo"
    assert reclaimed.claimed_by_actor_id is None
