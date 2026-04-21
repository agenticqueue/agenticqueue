from __future__ import annotations

import datetime as dt
import json
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.audit import set_session_audit_context
from agenticqueue_api.config import (
    get_psycopg_connect_args,
    get_sqlalchemy_sync_database_url,
)
from agenticqueue_api.models import LearningRecord
from agenticqueue_api.seed import load_seed_fixture, seed_example_data


def _session_factory() -> sessionmaker[Session]:
    engine = sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        future=True,
        connect_args=get_psycopg_connect_args(),
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


def _learning_id(label: str) -> uuid.UUID:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"https://agenticqueue.ai/tests/learnings-view/{label}",
    )


def _merge_learning(
    session: Session,
    *,
    actor_id: uuid.UUID,
    task_id: uuid.UUID,
    learning_id: uuid.UUID,
    title: str,
    scope: str,
    status: str,
    confidence: str,
    evidence: list[str],
    created_at: dt.datetime,
) -> None:
    session.merge(
        LearningRecord(
            id=learning_id,
            task_id=task_id,
            owner_actor_id=actor_id,
            owner="example-admin",
            title=title,
            learning_type="pattern",
            what_happened="A repeated browser shell issue was rediscovered during AQ web work.",
            what_learned="Keep read-only browser views wired to real transport data.",
            action_rule="Verify UI slices against deterministic local data before shipping.",
            applies_when="A web view renders project memory or learnings.",
            does_not_apply_when="The view is a static marketing surface without backend state.",
            evidence=evidence,
            scope=scope,
            promotion_eligible=scope != "global",
            confidence=confidence,
            status=status,
            review_date=dt.date(2026, 5, 20),
            embedding=None,
            created_at=created_at,
            updated_at=created_at,
        ),
    )


def main() -> None:
    fixture = load_seed_fixture()
    session_factory = _session_factory()
    with session_factory() as session:
        set_session_audit_context(
            session,
            actor_id=None,
            trace_id="aq-learnings-view-seed",
        )
        seeded = seed_example_data(session, fixture)
        _merge_learning(
            session,
            actor_id=seeded.actor_id,
            task_id=seeded.task_id,
            learning_id=_learning_id("tier-1-active"),
            title="Keep browser smoke coverage tied to deterministic local data",
            scope="task",
            status="active",
            confidence="confirmed",
            evidence=[
                "artifact://playwright-smoke",
                "tests/web/seed_learnings_view.py",
            ],
            created_at=dt.datetime(2026, 4, 20, 12, 0, tzinfo=dt.timezone.utc),
        )
        _merge_learning(
            session,
            actor_id=seeded.actor_id,
            task_id=seeded.task_id,
            learning_id=_learning_id("tier-2-active"),
            title="Promote repeated UI verification patterns to project scope",
            scope="project",
            status="active",
            confidence="validated",
            evidence=["artifact://phase-7-review"],
            created_at=dt.datetime(2026, 4, 20, 13, 0, tzinfo=dt.timezone.utc),
        )
        _merge_learning(
            session,
            actor_id=seeded.actor_id,
            task_id=seeded.task_id,
            learning_id=_learning_id("tier-3-superseded"),
            title="Old shell workaround superseded by the shared view components",
            scope="global",
            status="superseded",
            confidence="tentative",
            evidence=["artifact://legacy-shell"],
            created_at=dt.datetime(2026, 4, 19, 15, 0, tzinfo=dt.timezone.utc),
        )
        session.commit()

    print(json.dumps({"api_token": seeded.api_token}, sort_keys=True))


if __name__ == "__main__":
    main()
