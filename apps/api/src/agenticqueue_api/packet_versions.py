"""Helpers for immutable packet version storage and lookup."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Any, Mapping

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agenticqueue_api.models import PacketVersionModel, PacketVersionRecord
from agenticqueue_api.models.shared import SchemaModel


def _normalize_packet_payload(
    packet: Mapping[str, Any] | SchemaModel,
) -> dict[str, Any]:
    if isinstance(packet, SchemaModel):
        payload = packet.model_dump(mode="json")
    else:
        payload = dict(packet)

    payload["packet_version_id"] = str(payload.get("packet_version_id") or "")
    return payload


def canonical_packet_json(packet: Mapping[str, Any] | SchemaModel) -> str:
    """Return canonical packet JSON used for content hashing."""

    payload = _normalize_packet_payload(packet)
    payload.pop("packet_version_id", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def packet_content_hash(packet: Mapping[str, Any] | SchemaModel) -> str:
    """Return the sha256 hash of canonical packet JSON."""

    canonical = canonical_packet_json(packet)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def packet_version_uuid(packet_hash: str) -> uuid.UUID:
    """Return the deterministic UUID for one canonical packet hash."""

    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"https://agenticqueue.ai/packet-version/{packet_hash}",
    )


def get_packet_version_by_hash(
    session: Session,
    packet_hash: str,
) -> PacketVersionModel | None:
    """Return one persisted packet version by content hash."""

    record = session.scalar(
        sa.select(PacketVersionRecord).where(
            PacketVersionRecord.packet_hash == packet_hash
        )
    )
    if record is None:
        return None
    return PacketVersionModel.model_validate(record)


def get_current_packet_version(
    session: Session,
    task_id: uuid.UUID,
) -> PacketVersionModel | None:
    """Return the newest persisted packet version for one task."""

    record = session.scalar(
        sa.select(PacketVersionRecord)
        .where(PacketVersionRecord.task_id == task_id)
        .order_by(PacketVersionRecord.created_at.desc(), PacketVersionRecord.id.desc())
        .limit(1)
    )
    if record is None:
        return None
    return PacketVersionModel.model_validate(record)


def persist_packet_version(
    session: Session,
    task_id: uuid.UUID,
    packet: Mapping[str, Any] | SchemaModel,
) -> PacketVersionModel:
    """Persist one packet version if it does not already exist."""

    payload = _normalize_packet_payload(packet)
    packet_hash = packet_content_hash(payload)
    existing = get_packet_version_by_hash(session, packet_hash)
    if existing is not None:
        return existing

    version_id = packet_version_uuid(packet_hash)
    payload["packet_version_id"] = str(version_id)
    created_at = dt.datetime.now(dt.UTC)

    record = PacketVersionRecord(
        id=version_id,
        created_at=created_at,
        task_id=task_id,
        packet_hash=packet_hash,
        payload=payload,
    )
    try:
        with session.begin_nested():
            session.add(record)
            session.flush()
    except IntegrityError:
        existing = get_packet_version_by_hash(session, packet_hash)
        if existing is not None:
            return existing
        raise
    session.refresh(record)
    return PacketVersionModel.model_validate(record)


__all__ = [
    "canonical_packet_json",
    "get_current_packet_version",
    "get_packet_version_by_hash",
    "packet_content_hash",
    "packet_version_uuid",
    "persist_packet_version",
]
