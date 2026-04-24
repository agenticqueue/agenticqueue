from __future__ import annotations

# ruff: noqa: E402

import time
import uuid
from concurrent.futures import Future
from typing import cast

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

pytest_plugins = ["tests.aq.test_packet_assembler"]

from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.capabilities import grant_capability
from agenticqueue_api.app import create_app
from agenticqueue_api.compiler import (
    _cached_policy_registry,
    _cached_task_type_registry,
    compile_packet,
)
from agenticqueue_api.models import (
    CapabilityKey,
    CapabilityRecord,
    ProjectModel,
    TaskModel,
    TaskRecord,
    WorkspaceModel,
)
from agenticqueue_api.packet_cache import PacketCache
from agenticqueue_api.repo import (
    create_actor,
    create_project,
    create_task,
    create_workspace,
)
from fastapi.testclient import TestClient
from tests.aq.test_packet_assembler import (
    _actor_payload,
    _project_payload,
    _task_payload,
    _truncate_all_tables,
    _workspace_payload,
)


def _committed_session_factory(engine: sa.Engine) -> sessionmaker[Session]:
    _truncate_all_tables(engine)
    with engine.begin() as connection:
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
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed_prefetch_tasks(
    session_factory: sessionmaker[Session],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    actor_id = uuid.UUID("00000000-0000-0000-0000-000000000951")
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000952")
    project_id = uuid.UUID("00000000-0000-0000-0000-000000000953")
    first_task_id = uuid.UUID("00000000-0000-0000-0000-000000000954")
    second_task_id = uuid.UUID("00000000-0000-0000-0000-000000000955")
    third_task_id = uuid.UUID("00000000-0000-0000-0000-000000000956")

    with session_factory() as session:
        create_actor(session, _actor_payload(actor_id))
        create_workspace(session, _workspace_payload(workspace_id))
        create_project(session, _project_payload(project_id, workspace_id))
        create_task(
            session,
            _task_payload(
                task_id=first_task_id,
                project_id=project_id,
                title="Current packet task",
                spec="## Goal\nCompile the current packet.",
                created_at="2026-04-20T00:00:00+00:00",
            ),
        )
        create_task(
            session,
            _task_payload(
                task_id=second_task_id,
                project_id=project_id,
                title="Prefetch packet task one",
                spec="## Goal\nCompile the first prefetched packet.",
                created_at="2026-04-20T00:01:00+00:00",
            ),
        )
        create_task(
            session,
            _task_payload(
                task_id=third_task_id,
                project_id=project_id,
                title="Prefetch packet task two",
                spec="## Goal\nCompile the second prefetched packet.",
                created_at="2026-04-20T00:02:00+00:00",
            ),
        )
        session.commit()

    return first_task_id, second_task_id, third_task_id


def _seed_task_with_token(
    session_factory: sessionmaker[Session],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, str]:
    actor_id = uuid.UUID("00000000-0000-0000-0000-000000000961")
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000962")
    project_id = uuid.UUID("00000000-0000-0000-0000-000000000963")
    task_id = uuid.UUID("00000000-0000-0000-0000-000000000964")

    with session_factory() as session:
        actor = create_actor(session, _actor_payload(actor_id))
        workspace = create_workspace(
            session,
            WorkspaceModel.model_validate(
                {
                    "id": str(workspace_id),
                    "slug": "packet-cache-workspace",
                    "name": "Packet Cache Workspace",
                    "description": "Packet cache tests",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        project = create_project(
            session,
            ProjectModel.model_validate(
                {
                    "id": str(project_id),
                    "workspace_id": str(workspace.id),
                    "slug": "packet-cache-project",
                    "name": "Packet Cache Project",
                    "description": "Packet cache tests",
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        task = create_task(
            session,
            TaskModel.model_validate(
                {
                    "id": str(task_id),
                    "project_id": str(project.id),
                    "task_type": "coding-task",
                    "title": "Cache one packet over REST",
                    "state": "queued",
                    "description": "Compile one packet over HTTP.",
                    "contract": {
                        "repo": "github.com/agenticqueue/agenticqueue",
                        "branch": "main",
                        "spec": "## Goal\nCompile a packet over REST.",
                        "file_scope": [
                            "apps/api/src/agenticqueue_api/compiler.py",
                            "tests/aq/test_packet_cache.py",
                        ],
                        "surface_area": ["packet", "cache"],
                        "dod_checklist": ["Return one packet."],
                        "output": {},
                    },
                    "definition_of_done": ["Return one packet."],
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        grant_capability(
            session,
            actor_id=actor.id,
            capability=CapabilityKey.QUERY_GRAPH,
            scope={"project_id": str(project.id)},
            granted_by_actor_id=actor.id,
        )
        _, token = issue_api_token(
            session,
            actor_id=actor.id,
            scopes=[],
            expires_at=None,
        )
        session.commit()
        return actor.id, project.id, task.id, token


def test_packet_cache_prefetch_and_hit_rate(engine: sa.Engine) -> None:
    session_factory = _committed_session_factory(engine)
    first_task_id, second_task_id, third_task_id = _seed_prefetch_tasks(session_factory)
    _cached_task_type_registry()
    _cached_policy_registry()
    cache = PacketCache(
        session_factory=session_factory,
        ttl_seconds=60,
        max_entries=200,
        prefetch_width=2,
    )

    try:
        with session_factory() as session:
            first_packet = compile_packet(
                session,
                first_task_id,
                packet_cache=cache,
            )
            session.commit()

        assert cache.wait_for_prefetch(
            [second_task_id, third_task_id],
            learning_limit=5,
            timeout_seconds=1.0,
        )

        with session_factory() as session:
            started = time.perf_counter()
            cached_packet = compile_packet(
                session,
                first_task_id,
                packet_cache=cache,
            )
            hit_elapsed_ms = (time.perf_counter() - started) * 1000

        assert cached_packet == first_packet
        assert hit_elapsed_ms < 50

        for _ in range(9):
            with session_factory() as session:
                compile_packet(
                    session,
                    first_task_id,
                    packet_cache=cache,
                )

        stats = cache.stats()
        assert stats.misses == 1
        assert stats.hits == 10
        assert stats.hit_rate > 0.85
        assert stats.miss_reasons == {"empty": 1}
    finally:
        cache.close()


def test_packet_cache_internal_branches(engine: sa.Engine) -> None:
    session_factory = _committed_session_factory(engine)
    first_task_id, second_task_id, third_task_id = _seed_prefetch_tasks(session_factory)
    cache = PacketCache(
        session_factory=session_factory,
        ttl_seconds=60,
        max_entries=1,
        prefetch_width=1,
    )

    try:
        cache.start()
        cache.start()

        with session_factory() as session:
            first_packet = compile_packet(
                session,
                first_task_id,
                packet_cache=cache,
            )
            session.commit()
            cache.put(session, uuid.uuid4(), first_packet, learning_limit=5)

        assert not cache.wait_for_prefetch(
            [uuid.uuid4()],
            learning_limit=5,
            timeout_seconds=0.0,
        )
        assert cache._next_prefetch_candidates(uuid.uuid4()) == []

        with session_factory() as session:
            third_packet = compile_packet(
                session,
                third_task_id,
                packet_cache=cache,
            )
            session.commit()
            cache.put(session, second_task_id, third_packet, learning_limit=5)
            cache.put(session, third_task_id, third_packet, learning_limit=5)

        assert not cache.has_cached(first_task_id, learning_limit=5)
        cache._prefetch_one(third_task_id, 5)

        pending = Future[None]()
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            cache,
            "_next_prefetch_candidates",
            lambda task_id: [second_task_id, second_task_id],
        )
        monkeypatch.setattr(cache._executor, "submit", lambda *args, **kwargs: pending)
        try:
            cache.schedule_prefetch(first_task_id, learning_limit=5)
        finally:
            monkeypatch.undo()
        pending.set_result(None)

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            cache,
            "_next_prefetch_candidates",
            lambda task_id: [third_task_id],
        )
        try:
            cache.schedule_prefetch(first_task_id, learning_limit=5)
        finally:
            monkeypatch.undo()

        assert (
            cache.handle_invalidation({"invalidate_all": True, "reason": "manual"}) >= 1
        )
        assert (
            cache.handle_invalidation(
                {"project_id": str(uuid.uuid4()), "reason": "project-miss"}
            )
            == 0
        )
        assert cache.handle_invalidation({"reason": "missing-project"}) == 0
        assert cache.handle_invalidation({"project_id": "not-a-uuid"}) == 0
        assert cache.handle_invalidation("not-json") == 0
        assert cache.handle_invalidation("[]") == 0
    finally:
        cache.close()


def test_packet_cache_expires_and_handles_global_invalidations(
    engine: sa.Engine,
) -> None:
    session_factory = _committed_session_factory(engine)
    task_id, _, _ = _seed_prefetch_tasks(session_factory)
    cache = PacketCache(
        session_factory=session_factory,
        ttl_seconds=1,
        # Prefetch can fill the tiny LRU before this test inspects the expired entry.
        max_entries=4,
        prefetch_width=1,
    )

    try:
        with session_factory() as session:
            packet = compile_packet(
                session,
                task_id,
                packet_cache=cache,
            )
            session.commit()
            cache.put(session, task_id, packet, learning_limit=5)

        with cache._lock:
            entry = cache._entries[(task_id, 5)]
            cache._entries[(task_id, 5)] = type(entry)(
                task_id=entry.task_id,
                project_id=entry.project_id,
                learning_limit=entry.learning_limit,
                payload=entry.payload,
                cached_at=entry.cached_at - 2,
            )

        with session_factory() as session:
            cache.put(session, task_id, packet, learning_limit=7)

        with cache._lock:
            entry = cache._entries[(task_id, 7)]
            cache._entries[(task_id, 7)] = type(entry)(
                task_id=entry.task_id,
                project_id=entry.project_id,
                learning_limit=entry.learning_limit,
                payload=entry.payload,
                cached_at=entry.cached_at - 2,
            )

        assert not cache.has_cached(task_id, learning_limit=7)
        assert cache.get(task_id, learning_limit=5) is None
        assert not cache.has_cached(task_id, learning_limit=5)
        assert cache.stats().miss_reasons["expired"] == 1

        removed = cache.handle_invalidation(
            '{"invalidate_all": true, "reason": "policy:update"}'
        )
        assert removed == 0
        assert cache.listener_error is None
    finally:
        cache.close()


def test_packet_cache_listener_invalidates_within_one_second(engine: sa.Engine) -> None:
    session_factory = _committed_session_factory(engine)
    _, _, task_id, token = _seed_task_with_token(session_factory)

    with TestClient(create_app(session_factory=session_factory)) as client:
        response = client.get(
            f"/tasks/{task_id}/packet",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        app = cast(FastAPI, client.app)
        packet_cache = cast(PacketCache, app.state.packet_cache)
        assert packet_cache.has_cached(task_id, learning_limit=5)
        assert packet_cache.listener_error is None

        with session_factory() as session:
            task = session.get(TaskRecord, task_id)
            assert task is not None
            task.description = "Mutated to trigger packet invalidation."
            session.commit()

        deadline = time.perf_counter() + 1.0
        while time.perf_counter() < deadline:
            if not packet_cache.has_cached(task_id, learning_limit=5):
                break
            time.sleep(0.02)

        assert not packet_cache.has_cached(task_id, learning_limit=5)
        assert packet_cache.stats().invalidations >= 1
