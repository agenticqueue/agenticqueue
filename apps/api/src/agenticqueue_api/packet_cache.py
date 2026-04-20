"""In-process packet cache with Postgres invalidation listeners."""

from __future__ import annotations

import copy
from collections import Counter, OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import json
import threading
import time
import uuid
from typing import Any, Callable

import psycopg
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.config import (
    get_direct_sync_database_url,
    get_packet_cache_max_entries,
    get_packet_cache_ttl_seconds,
    get_packet_prefetch_width,
)
from agenticqueue_api.models.task import TaskRecord

PACKET_INVALIDATION_CHANNEL = "packet_invalidate"
_FINISHED_STATES = ("done", "cancelled")


@dataclass(slots=True)
class PacketCacheEntry:
    """One cached packet payload."""

    task_id: uuid.UUID
    project_id: uuid.UUID | None
    learning_limit: int
    payload: dict[str, Any]
    cached_at: float


@dataclass(slots=True)
class PacketCacheStats:
    """Snapshot of cache hit/miss and invalidation counters."""

    hits: int
    misses: int
    hit_rate: float
    miss_reasons: dict[str, int]
    invalidations: int


class PacketCache:
    """Project-scoped LRU cache for compiled packets."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        max_entries: int | None = None,
        ttl_seconds: int | None = None,
        prefetch_width: int | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._max_entries = max_entries or get_packet_cache_max_entries()
        self._ttl_seconds = ttl_seconds or get_packet_cache_ttl_seconds()
        self._prefetch_width = max(1, prefetch_width or get_packet_prefetch_width())
        self._clock = clock or time.monotonic
        self._entries: OrderedDict[tuple[uuid.UUID, int], PacketCacheEntry] = (
            OrderedDict()
        )
        self._prefetch_futures: dict[tuple[uuid.UUID, int], Future[None]] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=self._prefetch_width,
            thread_name_prefix="aq-packet-prefetch",
        )
        self._stop_event = threading.Event()
        self._listener_thread: threading.Thread | None = None
        self._listener_error: str | None = None
        self._hits = 0
        self._misses = 0
        self._invalidations = 0
        self._miss_reasons: Counter[str] = Counter()

    def start(self) -> None:
        """Start the Postgres LISTEN loop once for this cache."""

        if self._listener_thread is not None:
            return
        self._stop_event.clear()
        self._listener_thread = threading.Thread(
            target=self._listen_loop,
            name="aq-packet-invalidation-listener",
            daemon=True,
        )
        self._listener_thread.start()

    def close(self) -> None:
        """Stop background workers and close the invalidation listener."""

        self._stop_event.set()
        if self._listener_thread is not None:
            self._listener_thread.join(timeout=1.0)
            self._listener_thread = None
        self._executor.shutdown(wait=True, cancel_futures=True)

    @property
    def listener_error(self) -> str | None:
        """Return the most recent listener failure, if any."""

        return self._listener_error

    def stats(self) -> PacketCacheStats:
        """Return current cache counters."""

        with self._lock:
            total = self._hits + self._misses
            hit_rate = 0.0 if total == 0 else self._hits / total
            return PacketCacheStats(
                hits=self._hits,
                misses=self._misses,
                hit_rate=hit_rate,
                miss_reasons=dict(self._miss_reasons),
                invalidations=self._invalidations,
            )

    def has_cached(
        self,
        task_id: uuid.UUID,
        *,
        learning_limit: int,
    ) -> bool:
        """Return whether a non-expired packet is cached for one task."""

        with self._lock:
            entry = self._entries.get((task_id, learning_limit))
            if entry is None:
                return False
            if self._is_expired(entry):
                self._entries.pop((task_id, learning_limit), None)
                return False
            return True

    def get(
        self,
        task_id: uuid.UUID,
        *,
        learning_limit: int,
    ) -> dict[str, Any] | None:
        """Return a cached packet payload or record a cache miss."""

        key = (task_id, learning_limit)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._record_miss("empty")
                return None
            if self._is_expired(entry):
                self._entries.pop(key, None)
                self._record_miss("expired")
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return copy.deepcopy(entry.payload)

    def put(
        self,
        session: Session,
        task_id: uuid.UUID,
        payload: dict[str, Any],
        *,
        learning_limit: int,
    ) -> None:
        """Store one packet payload in the LRU."""

        task = session.get(TaskRecord, task_id)
        project_id = None if task is None else task.project_id
        entry = PacketCacheEntry(
            task_id=task_id,
            project_id=project_id,
            learning_limit=learning_limit,
            payload=copy.deepcopy(payload),
            cached_at=self._clock(),
        )
        key = (task_id, learning_limit)
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def schedule_prefetch(
        self,
        task_id: uuid.UUID,
        *,
        learning_limit: int,
    ) -> None:
        """Kick off async compilation for the next two likely tasks."""

        for candidate_id in self._next_prefetch_candidates(task_id):
            key = (candidate_id, learning_limit)
            with self._lock:
                future = self._prefetch_futures.get(key)
                if future is not None and not future.done():
                    continue
                if self._entries.get(key) is not None and not self._is_expired(
                    self._entries[key]
                ):
                    continue
                future = self._executor.submit(
                    self._prefetch_one,
                    candidate_id,
                    learning_limit,
                )
                self._prefetch_futures[key] = future
                future.add_done_callback(self._make_prefetch_done_callback(key))

    def wait_for_prefetch(
        self,
        task_ids: list[uuid.UUID],
        *,
        learning_limit: int,
        timeout_seconds: float,
    ) -> bool:
        """Wait for a set of prefetched tasks to appear in cache."""

        deadline = self._clock() + timeout_seconds
        while self._clock() < deadline:
            if all(
                self.has_cached(task_id, learning_limit=learning_limit)
                for task_id in task_ids
            ):
                return True
            time.sleep(0.01)
        return all(
            self.has_cached(task_id, learning_limit=learning_limit)
            for task_id in task_ids
        )

    def handle_invalidation(self, payload: dict[str, Any] | str) -> int:
        """Invalidate cached packets using a project-scoped notification."""

        message = self._normalize_payload(payload)
        reason = str(message.get("reason") or "unknown")
        if bool(message.get("invalidate_all")):
            return self._invalidate_all(reason)

        project_id_raw = message.get("project_id")
        if not isinstance(project_id_raw, str):
            return self._invalidate_all(reason)

        try:
            project_id = uuid.UUID(project_id_raw)
        except ValueError:
            return self._invalidate_all(reason)
        return self._invalidate_project(project_id, reason)

    def _normalize_payload(self, payload: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(payload, dict):
            return dict(payload)
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return {"invalidate_all": True, "reason": "invalid-json"}
        return decoded if isinstance(decoded, dict) else {}

    def _clear_prefetch_future(self, key: tuple[uuid.UUID, int]) -> None:
        with self._lock:
            self._prefetch_futures.pop(key, None)

    def _make_prefetch_done_callback(
        self,
        key: tuple[uuid.UUID, int],
    ) -> Callable[[Future[None]], None]:
        def _callback(_future: Future[None]) -> None:
            self._clear_prefetch_future(key)

        return _callback

    def _record_miss(self, reason: str) -> None:
        self._misses += 1
        self._miss_reasons[reason] += 1

    def _is_expired(self, entry: PacketCacheEntry) -> bool:
        return (self._clock() - entry.cached_at) >= self._ttl_seconds

    def _invalidate_all(self, reason: str) -> int:
        del reason
        with self._lock:
            removed = len(self._entries)
            self._entries.clear()
            if removed:
                self._invalidations += removed
            return removed

    def _invalidate_project(self, project_id: uuid.UUID, reason: str) -> int:
        del reason
        with self._lock:
            keys = [
                key
                for key, entry in self._entries.items()
                if entry.project_id == project_id
            ]
            for key in keys:
                self._entries.pop(key, None)
            if keys:
                self._invalidations += len(keys)
            return len(keys)

    def _next_prefetch_candidates(self, task_id: uuid.UUID) -> list[uuid.UUID]:
        with self._session_factory() as session:
            current = session.get(TaskRecord, task_id)
            if current is None:
                return []

            rows = session.scalars(
                sa.select(TaskRecord.id)
                .where(TaskRecord.project_id == current.project_id)
                .where(TaskRecord.id != current.id)
                .where(~TaskRecord.state.in_(_FINISHED_STATES))
                .order_by(
                    TaskRecord.priority.desc(),
                    TaskRecord.sequence.asc(),
                    TaskRecord.id.asc(),
                )
                .limit(self._prefetch_width)
            ).all()
            return list(rows)

    def _prefetch_one(self, task_id: uuid.UUID, learning_limit: int) -> None:
        if self.has_cached(task_id, learning_limit=learning_limit):
            return

        from agenticqueue_api.compiler import assemble_packet
        from agenticqueue_api.packet_versions import persist_packet_version

        with self._session_factory() as session:
            packet = assemble_packet(session, task_id, learning_limit=learning_limit)
            packet_version = persist_packet_version(session, task_id, packet)
            self.put(
                session,
                task_id,
                dict(packet_version.payload),
                learning_limit=learning_limit,
            )
            session.commit()

    def _listen_loop(self) -> None:
        try:
            with psycopg.connect(
                get_direct_sync_database_url(),
                autocommit=True,
                prepare_threshold=None,
            ) as connection:
                connection.execute(f"LISTEN {PACKET_INVALIDATION_CHANNEL}")
                while not self._stop_event.is_set():
                    for notify in connection.notifies(timeout=0.2, stop_after=1):
                        self.handle_invalidation(notify.payload)
        except Exception as error:  # pragma: no cover - exercised in listener tests
            self._listener_error = str(error)


__all__ = [
    "PACKET_INVALIDATION_CHANNEL",
    "PacketCache",
    "PacketCacheEntry",
    "PacketCacheStats",
]
