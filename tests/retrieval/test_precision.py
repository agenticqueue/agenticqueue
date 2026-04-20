from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models.learning import LearningRecord
from agenticqueue_api.models.project import ProjectRecord
from agenticqueue_api.models.task import TaskRecord
from agenticqueue_api.models.workspace import WorkspaceRecord
from agenticqueue_api.retrieval import RetrievalQuery, RetrievalService
from agenticqueue_api.schemas.learning import LearningStatus

TRUNCATE_TABLES = (
    "artifact",
    "decision",
    "run",
    "learning",
    "task",
    "project",
    "workspace",
    "audit_log",
)
CORPUS_PATH = Path(__file__).with_name("golden_corpus.json")
EXPECTED_CORPUS_SIZE = 50
PRECISION_AT_5_FLOOR = 0.75
PRECISION_AT_10_FLOOR = 0.80
BASELINE_PRECISION_AT_5 = 0.956
BASELINE_PRECISION_AT_10 = 0.980
MAX_REGRESSION_POINTS = 0.02
BASE_TIME = dt.datetime(2026, 4, 20, 18, 0, tzinfo=dt.UTC)


@dataclass(frozen=True)
class FamilyFixture:
    slug: str
    surface_area: tuple[str, ...]
    file_scope: str
    learning_titles: tuple[str, ...]


@dataclass(frozen=True)
class CorpusCase:
    id: str
    expected_family: str
    mode: str
    title: str
    fuzzy_global_search: bool


FAMILY_FIXTURES = {
    "validator-payload-retry": FamilyFixture(
        slug="validator-payload-retry",
        surface_area=("retrieval/validator", "contract-engine"),
        file_scope="apps/api/src/agenticqueue_api/validators/retry.py",
        learning_titles=(
            "Validator payload retry normalization",
            "Validator payload retry coercion guard",
            "Validator payload retry contract trim",
            "Validator payload retry audit note",
            "Validator payload retry schema fallback",
        ),
    ),
    "audit-ledger-hash-chain": FamilyFixture(
        slug="audit-ledger-hash-chain",
        surface_area=("audit/ledger", "worm-chain"),
        file_scope="apps/api/src/agenticqueue_api/audit/ledger.py",
        learning_titles=(
            "Audit ledger hash chain bootstrap",
            "Audit ledger hash chain rollover guard",
            "Audit ledger hash chain replay proof",
            "Audit ledger hash chain snapshot verifier",
            "Audit ledger hash chain tamper rejection",
        ),
    ),
    "pgbouncer-transaction-pooling": FamilyFixture(
        slug="pgbouncer-transaction-pooling",
        surface_area=("pooling/pgbouncer", "transaction-pool"),
        file_scope="apps/api/src/agenticqueue_api/db/pool.py",
        learning_titles=(
            "PgBouncer transaction pooling defaults",
            "PgBouncer transaction pooling healthcheck",
            "PgBouncer transaction pooling timeout floor",
            "PgBouncer transaction pooling prepared statement guard",
            "PgBouncer transaction pooling telemetry note",
        ),
    ),
    "statement-timeout-traversal": FamilyFixture(
        slug="statement-timeout-traversal",
        surface_area=("timeouts/traversal", "statement-timeout"),
        file_scope="apps/api/src/agenticqueue_api/db/timeouts.py",
        learning_titles=(
            "Statement timeout traversal defaults",
            "Statement timeout traversal decorator override",
            "Statement timeout traversal regression guard",
            "Statement timeout traversal pool sync",
            "Statement timeout traversal metrics note",
        ),
    ),
    "redaction-entropy-scanner": FamilyFixture(
        slug="redaction-entropy-scanner",
        surface_area=("security/redaction", "entropy-scan"),
        file_scope="apps/api/src/agenticqueue_api/security/redaction.py",
        learning_titles=(
            "Redaction entropy scanner baseline",
            "Redaction entropy scanner payload cap ordering",
            "Redaction entropy scanner false positive tune",
            "Redaction entropy scanner audit breadcrumb",
            "Redaction entropy scanner blocking path",
        ),
    ),
    "trigram-retrieval-fallback": FamilyFixture(
        slug="trigram-retrieval-fallback",
        surface_area=("retrieval/trgm", "fts-fallback"),
        file_scope="apps/api/src/agenticqueue_api/retrieval/tiers/trgm.py",
        learning_titles=(
            "Trigram retrieval fallback candidate ordering",
            "Trigram retrieval fallback typo floor",
            "Trigram retrieval fallback lexical blend",
            "Trigram retrieval fallback duplicate suppression",
            "Trigram retrieval fallback scoring note",
        ),
    ),
    "packet-cache-invalidation": FamilyFixture(
        slug="packet-cache-invalidation",
        surface_area=("packets/cache", "packet-invalidation"),
        file_scope="apps/api/src/agenticqueue_api/packets/cache.py",
        learning_titles=(
            "Packet cache invalidation on graph mutation",
            "Packet cache invalidation stale hash purge",
            "Packet cache invalidation downstream fanout",
            "Packet cache invalidation listen notify bridge",
            "Packet cache invalidation warm path note",
        ),
    ),
    "capability-grant-matrix": FamilyFixture(
        slug="capability-grant-matrix",
        surface_area=("capabilities/matrix", "grant-deny"),
        file_scope="apps/api/src/agenticqueue_api/capabilities/matrix.py",
        learning_titles=(
            "Capability grant matrix allow path",
            "Capability grant matrix deny path",
            "Capability grant matrix actor scoping",
            "Capability grant matrix audit mirror",
            "Capability grant matrix fixture reuse",
        ),
    ),
    "idempotency-replay-key": FamilyFixture(
        slug="idempotency-replay-key",
        surface_area=("idempotency/replay", "dedupe-key"),
        file_scope="apps/api/src/agenticqueue_api/http/idempotency.py",
        learning_titles=(
            "Idempotency replay key cache write",
            "Idempotency replay key status code mirror",
            "Idempotency replay key expiry sweep",
            "Idempotency replay key conflict branch",
            "Idempotency replay key header parse",
        ),
    ),
    "mcp-transport-parity": FamilyFixture(
        slug="mcp-transport-parity",
        surface_area=("transport/mcp", "parity-surface"),
        file_scope="apps/api/src/agenticqueue_api/mcp/learnings_tools.py",
        learning_titles=(
            "MCP transport parity payload shape",
            "MCP transport parity auth check",
            "MCP transport parity CLI mirror",
            "MCP transport parity REST mirror",
            "MCP transport parity fixture note",
        ),
    ),
}


def _uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{label}")


def _timestamp(offset_minutes: int) -> dt.datetime:
    return BASE_TIME + dt.timedelta(minutes=offset_minutes)


def _truncate_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        qualified_tables = ", ".join(
            f"agenticqueue.{table_name}" for table_name in TRUNCATE_TABLES
        )
        connection.execute(
            sa.text(f"TRUNCATE TABLE {qualified_tables} RESTART IDENTITY CASCADE")
        )


def _load_corpus() -> list[CorpusCase]:
    payload = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise AssertionError("golden corpus must be a JSON array")
    cases = [CorpusCase(**item) for item in payload]
    if len(cases) != EXPECTED_CORPUS_SIZE:
        raise AssertionError(
            f"expected {EXPECTED_CORPUS_SIZE} corpus queries, found {len(cases)}"
        )
    seen_ids: set[str] = set()
    for case in cases:
        if case.id in seen_ids:
            raise AssertionError(f"duplicate corpus id: {case.id}")
        seen_ids.add(case.id)
        if case.expected_family not in FAMILY_FIXTURES:
            raise AssertionError(f"unknown family for {case.id}: {case.expected_family}")
    return cases


def _seed_project(session: Session) -> uuid.UUID:
    workspace = WorkspaceRecord(
        id=_uuid("retrieval-golden-workspace"),
        slug="retrieval-golden-workspace",
        name="Retrieval Golden Workspace",
        description="Workspace for retrieval precision golden-corpus tests",
        created_at=_timestamp(0),
        updated_at=_timestamp(0),
    )
    session.add(workspace)
    session.flush()

    project = ProjectRecord(
        id=_uuid("retrieval-golden-project"),
        workspace_id=workspace.id,
        slug="retrieval-golden-project",
        name="Retrieval Golden Project",
        description="Project for retrieval precision golden-corpus tests",
        created_at=_timestamp(1),
        updated_at=_timestamp(1),
    )
    session.add(project)
    session.flush()
    return project.id


def _seed_task(
    session: Session,
    *,
    label: str,
    project_id: uuid.UUID,
    title: str,
    description: str,
    spec: str,
    surface_area: list[str],
    file_scope: list[str],
    created_at: dt.datetime,
) -> uuid.UUID:
    task = TaskRecord(
        id=_uuid(label),
        project_id=project_id,
        task_type="coding-task",
        title=title,
        state="queued",
        description=description,
        contract={
            "surface_area": surface_area,
            "file_scope": file_scope,
            "spec": spec,
        },
        definition_of_done=["retrieval precision covered"],
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(task)
    session.flush()
    return task.id


def _seed_learning(
    session: Session,
    *,
    label: str,
    task_id: uuid.UUID,
    title: str,
    family: FamilyFixture,
    created_at: dt.datetime,
) -> None:
    learning = LearningRecord(
        id=_uuid(label),
        task_id=task_id,
        owner_actor_id=None,
        owner="retrieval-golden",
        title=title,
        learning_type="pattern",
        what_happened=f"{title} regressed while hardening {family.slug}.",
        what_learned=f"Keep {family.slug} changes grouped under the same retrieval family.",
        action_rule=f"Prefer {title.lower()} when working in {family.surface_area[0]}.",
        applies_when=f"Work touches {family.surface_area[0]} or {family.file_scope}.",
        does_not_apply_when="The query targets an unrelated subsystem family.",
        evidence=[family.file_scope],
        scope="project",
        promotion_eligible=False,
        confidence="confirmed",
        status=LearningStatus.ACTIVE.value,
        review_date=None,
        embedding=None,
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(learning)
    session.flush()


def _seed_family(
    session: Session,
    *,
    project_id: uuid.UUID,
    family: FamilyFixture,
    offset_minutes: int,
) -> None:
    for index, learning_title in enumerate(family.learning_titles):
        task_created_at = _timestamp(offset_minutes + index * 2)
        learning_created_at = _timestamp(offset_minutes + index * 2 + 1)
        task_id = _seed_task(
            session,
            label=f"{family.slug}-source-task-{index}",
            project_id=project_id,
            title=f"{learning_title} source task",
            description=f"Seed task for the {family.slug} retrieval family.",
            spec=f"Stabilize {learning_title.lower()} in the retrieval golden corpus.",
            surface_area=list(family.surface_area),
            file_scope=[family.file_scope],
            created_at=task_created_at,
        )
        _seed_learning(
            session,
            label=f"{family.slug}-learning-{index}",
            task_id=task_id,
            title=learning_title,
            family=family,
            created_at=learning_created_at,
        )


def _query_shape(case: CorpusCase, family: FamilyFixture) -> tuple[str, str, list[str], list[str]]:
    if case.mode == "surface_exact":
        return (
            f"Exercise {family.slug} via a direct surface-area hit.",
            case.title,
            list(family.surface_area),
            [family.file_scope],
        )
    if case.mode == "surface_filescope":
        return (
            f"Keep {family.slug} ranked with matching file scope and the family token intact.",
            f"Use {family.surface_area[0]} evidence to verify {case.title.lower()}.",
            list(family.surface_area),
            [family.file_scope],
        )
    if case.mode == "fts_exact":
        return (
            f"Force the cold path for {family.slug} without a surface-area overlap.",
            f"Lexical retrieval should still recover the {family.slug} family.",
            [f"query/{family.slug}"],
            [f"tests/queries/{family.slug}.md"],
        )
    if case.mode == "fts_filescope":
        return (
            f"Cold-path lexical search for {family.slug} using file scope hints.",
            f"Match {family.file_scope} and {case.title.lower()} without relying on shared surface tags.",
            [f"query/{family.slug}-lexical"],
            [family.file_scope],
        )
    if case.mode == "trgm_typo":
        return (
            "",
            case.title,
            ["zzscope"],
            ["tmp/zzseed.bin"],
        )
    raise AssertionError(f"unsupported corpus mode: {case.mode}")


def _seed_query_tasks(
    session: Session,
    *,
    project_id: uuid.UUID,
    corpus: list[CorpusCase],
    offset_minutes: int,
) -> dict[str, uuid.UUID]:
    task_ids: dict[str, uuid.UUID] = {}
    for index, case in enumerate(corpus):
        family = FAMILY_FIXTURES[case.expected_family]
        description, spec, surface_area, file_scope = _query_shape(case, family)
        task_ids[case.id] = _seed_task(
            session,
            label=f"query-task-{case.id}",
            project_id=project_id,
            title=case.title,
            description=description,
            spec=spec,
            surface_area=surface_area,
            file_scope=file_scope,
            created_at=_timestamp(offset_minutes + index),
        )
    return task_ids


def _precision_at_k(
    retrieved_titles: list[str],
    expected_titles: tuple[str, ...],
    *,
    k: int,
) -> float:
    denominator = min(k, len(expected_titles))
    if denominator == 0:
        raise AssertionError("expected titles must not be empty")
    expected = set(expected_titles)
    hits = sum(1 for title in retrieved_titles[:k] if title in expected)
    return hits / denominator


def _worst_case_summary(case_metrics: list[tuple[str, float, float, list[str], list[str]]]) -> str:
    worst = sorted(case_metrics, key=lambda item: (item[1], item[2], item[0]))[:5]
    return "; ".join(
        f"{case_id}: p@5={p5:.2f}, p@10={p10:.2f}, tiers={','.join(tiers)}, top5={top5}"
        for case_id, p5, p10, tiers, top5 in worst
    )


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture
def session(engine: Engine) -> Session:
    _truncate_tables(engine)
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


def test_golden_corpus_has_fifty_queries() -> None:
    assert len(_load_corpus()) == EXPECTED_CORPUS_SIZE


def test_precision_golden_corpus_meets_baseline(session: Session) -> None:
    corpus = _load_corpus()
    project_id = _seed_project(session)

    family_stride = 20
    for index, family in enumerate(FAMILY_FIXTURES.values()):
        _seed_family(
            session,
            project_id=project_id,
            family=family,
            offset_minutes=10 + index * family_stride,
        )

    query_task_ids = _seed_query_tasks(
        session,
        project_id=project_id,
        corpus=corpus,
        offset_minutes=10 + len(FAMILY_FIXTURES) * family_stride,
    )
    session.commit()

    service = RetrievalService(session)
    precision_at_5_scores: list[float] = []
    precision_at_10_scores: list[float] = []
    case_metrics: list[tuple[str, float, float, list[str], list[str]]] = []
    trgm_tier_hits = 0

    for case in corpus:
        result = service.retrieve(
            RetrievalQuery(
                task_id=query_task_ids[case.id],
                k=10,
                fuzzy_global_search=case.fuzzy_global_search,
            )
        )
        expected_titles = FAMILY_FIXTURES[case.expected_family].learning_titles
        retrieved_titles = [learning.title for learning in result.items]
        precision_at_5 = _precision_at_k(retrieved_titles, expected_titles, k=5)
        precision_at_10 = _precision_at_k(retrieved_titles, expected_titles, k=10)
        precision_at_5_scores.append(precision_at_5)
        precision_at_10_scores.append(precision_at_10)
        case_metrics.append(
            (
                case.id,
                precision_at_5,
                precision_at_10,
                list(result.tiers_fired),
                retrieved_titles[:5],
            )
        )

        if case.mode.startswith("surface_"):
            assert "fts" not in result.tiers_fired, case.id
            assert "trgm" not in result.tiers_fired, case.id
        elif case.mode.startswith("fts_"):
            assert "fts" in result.tiers_fired, f"{case.id}: {result.tiers_fired}"
        elif case.mode == "trgm_typo":
            if "trgm" in result.tiers_fired:
                trgm_tier_hits += 1
            else:
                assert "fts" in result.tiers_fired, f"{case.id}: {result.tiers_fired}"

    precision_at_5 = sum(precision_at_5_scores) / len(precision_at_5_scores)
    precision_at_10 = sum(precision_at_10_scores) / len(precision_at_10_scores)
    worst_cases = _worst_case_summary(case_metrics)

    assert precision_at_5 >= PRECISION_AT_5_FLOOR, (
        f"Precision@5 dropped to {precision_at_5:.2f}; worst cases: {worst_cases}"
    )
    assert precision_at_10 >= PRECISION_AT_10_FLOOR, (
        f"Precision@10 dropped to {precision_at_10:.2f}; worst cases: {worst_cases}"
    )
    assert precision_at_5 >= BASELINE_PRECISION_AT_5 - MAX_REGRESSION_POINTS, (
        f"Precision@5 regressed below the 2pp budget: {precision_at_5:.2f}; "
        f"worst cases: {worst_cases}"
    )
    assert precision_at_10 >= BASELINE_PRECISION_AT_10 - MAX_REGRESSION_POINTS, (
        f"Precision@10 regressed below the 2pp budget: {precision_at_10:.2f}; "
        f"worst cases: {worst_cases}"
    )
    assert trgm_tier_hits > 0, "golden corpus never exercised the trigram tier"
