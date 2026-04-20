from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
import yaml  # type: ignore[import-untyped]
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.app import create_app
from agenticqueue_api.auth import issue_api_token
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import ActorModel, CapabilityKey, CapabilityRecord
from agenticqueue_api.repo import create_actor
from agenticqueue_api.task_type_registry import SchemaLoadError, TaskTypeRegistry

VALID_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "coding-task",
    "type": "object",
    "properties": {
        "surface_area": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
        }
    },
    "required": ["surface_area"],
    "additionalProperties": False,
}
VALID_POLICY = {"hitl_required": False, "autonomy_tier": 3}
TRUNCATE_TABLES = [
    "api_token",
    "capability_grant",
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


def _write_task_type(
    directory: Path,
    *,
    name: str,
    schema: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if schema is not None:
        (directory / f"{name}.schema.json").write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if policy is not None:
        (directory / f"{name}.policy.yaml").write_text(
            yaml.safe_dump(policy, sort_keys=True),
            encoding="utf-8",
        )


def _seed_capabilities(engine: Engine) -> None:
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


def truncate_all_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        qualified_tables = ", ".join(
            f"agenticqueue.{table_name}" for table_name in TRUNCATE_TABLES
        )
        connection.execute(
            sa.text(f"TRUNCATE TABLE {qualified_tables} RESTART IDENTITY CASCADE")
        )
    _seed_capabilities(engine)


def seed_actor(
    session_factory: sessionmaker[Session],
    *,
    handle: str,
    actor_type: str,
    display_name: str,
) -> ActorModel:
    with session_factory() as session:
        actor = create_actor(
            session,
            ActorModel.model_validate(
                {
                    "id": str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"https://agenticqueue.ai/tests/{handle}",
                        )
                    ),
                    "handle": handle,
                    "actor_type": actor_type,
                    "display_name": display_name,
                    "auth_subject": f"{handle}-subject",
                    "is_active": True,
                    "created_at": "2026-04-20T00:00:00+00:00",
                    "updated_at": "2026-04-20T00:00:00+00:00",
                }
            ),
        )
        session.commit()
        return actor


def seed_token(
    session_factory: sessionmaker[Session],
    *,
    actor_id: Any,
    scopes: list[str],
) -> str:
    with session_factory() as session:
        _, raw_token = issue_api_token(
            session,
            actor_id=actor_id,
            scopes=scopes,
            expires_at=None,
        )
        session.commit()
        return raw_token


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> None:
    truncate_all_tables(engine)


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_task_type_registry_load_discovers_all_schema_files(tmp_path: Path) -> None:
    task_types_dir = tmp_path / "task_types"
    _write_task_type(
        task_types_dir, name="coding-task", schema=VALID_SCHEMA, policy=VALID_POLICY
    )
    _write_task_type(
        task_types_dir,
        name="review-task",
        schema={**VALID_SCHEMA, "title": "review-task"},
        policy={"hitl_required": True, "autonomy_tier": 2},
    )

    registry = TaskTypeRegistry(task_types_dir)
    registry.load()

    assert [definition.name for definition in registry.list()] == [
        "coding-task",
        "review-task",
    ]


def test_task_type_registry_missing_or_malformed_schema_raises_clear_errors(
    tmp_path: Path,
) -> None:
    with pytest.raises(SchemaLoadError, match="Task type directory not found"):
        TaskTypeRegistry(tmp_path / "missing-directory").load()

    assert (
        TaskTypeRegistry(
            tmp_path / "missing-signature", reload_enabled=True
        ).maybe_reload()
        is False
    )

    missing_schema_dir = tmp_path / "missing-schema"
    _write_task_type(missing_schema_dir, name="coding-task", policy=VALID_POLICY)

    missing_registry = TaskTypeRegistry(missing_schema_dir)
    with pytest.raises(SchemaLoadError, match="No task type schema files found"):
        missing_registry.load()

    missing_policy_dir = tmp_path / "missing-policy"
    _write_task_type(
        missing_policy_dir,
        name="coding-task",
        schema=VALID_SCHEMA,
    )

    with pytest.raises(SchemaLoadError, match="Missing policy file"):
        TaskTypeRegistry(missing_policy_dir).load()

    malformed_schema_dir = tmp_path / "malformed-schema"
    _write_task_type(
        malformed_schema_dir,
        name="coding-task",
        policy=VALID_POLICY,
    )
    (malformed_schema_dir / "coding-task.schema.json").write_text(
        '{"$schema":"https://json-schema.org/draft/2020-12/schema","type":7}\n',
        encoding="utf-8",
    )

    malformed_registry = TaskTypeRegistry(malformed_schema_dir)
    with pytest.raises(SchemaLoadError, match=r"coding-task\.schema\.json"):
        malformed_registry.load()

    invalid_json_dir = tmp_path / "invalid-json"
    _write_task_type(
        invalid_json_dir,
        name="coding-task",
        policy=VALID_POLICY,
    )
    (invalid_json_dir / "coding-task.schema.json").write_text(
        "{invalid-json}\n",
        encoding="utf-8",
    )

    with pytest.raises(SchemaLoadError, match=r"coding-task\.schema\.json"):
        TaskTypeRegistry(invalid_json_dir).load()

    schema_not_object_dir = tmp_path / "schema-not-object"
    _write_task_type(
        schema_not_object_dir,
        name="coding-task",
        policy=VALID_POLICY,
    )
    (schema_not_object_dir / "coding-task.schema.json").write_text(
        '["not-an-object"]\n',
        encoding="utf-8",
    )

    with pytest.raises(SchemaLoadError, match="must contain a JSON object"):
        TaskTypeRegistry(schema_not_object_dir).load()

    invalid_yaml_dir = tmp_path / "invalid-yaml"
    _write_task_type(
        invalid_yaml_dir,
        name="coding-task",
        schema=VALID_SCHEMA,
    )
    (invalid_yaml_dir / "coding-task.policy.yaml").write_text(
        ":\n",
        encoding="utf-8",
    )

    with pytest.raises(SchemaLoadError, match="Invalid policy file"):
        TaskTypeRegistry(invalid_yaml_dir).load()

    empty_policy_dir = tmp_path / "empty-policy"
    _write_task_type(
        empty_policy_dir,
        name="coding-task",
        schema=VALID_SCHEMA,
    )
    (empty_policy_dir / "coding-task.policy.yaml").write_text("", encoding="utf-8")

    empty_policy_registry = TaskTypeRegistry(empty_policy_dir)
    empty_policy_registry.load()
    assert empty_policy_registry.list()[0].policy == {}

    policy_not_mapping_dir = tmp_path / "policy-not-mapping"
    _write_task_type(
        policy_not_mapping_dir,
        name="coding-task",
        schema=VALID_SCHEMA,
    )
    (policy_not_mapping_dir / "coding-task.policy.yaml").write_text(
        "- not\n- a\n- mapping\n",
        encoding="utf-8",
    )

    with pytest.raises(SchemaLoadError, match="must contain a YAML mapping"):
        TaskTypeRegistry(policy_not_mapping_dir).load()

    orphan_policy_dir = tmp_path / "orphan-policy"
    _write_task_type(
        orphan_policy_dir,
        name="coding-task",
        schema=VALID_SCHEMA,
        policy=VALID_POLICY,
    )
    (orphan_policy_dir / "review-task.policy.yaml").write_text(
        "hitl_required: true\n",
        encoding="utf-8",
    )

    with pytest.raises(SchemaLoadError, match="Policy file has no matching schema"):
        TaskTypeRegistry(orphan_policy_dir).load()


def test_task_type_registry_hot_reload_picks_up_file_changes(tmp_path: Path) -> None:
    task_types_dir = tmp_path / "task_types"
    _write_task_type(
        task_types_dir, name="coding-task", schema=VALID_SCHEMA, policy=VALID_POLICY
    )
    (task_types_dir / "notes.txt").write_text("ignored\n", encoding="utf-8")
    (task_types_dir / "nested").mkdir()

    registry = TaskTypeRegistry(task_types_dir, reload_enabled=True)
    registry.load()

    assert registry.maybe_reload() is False
    assert registry.list()[0].schema["title"] == "coding-task"

    time.sleep(0.01)
    _write_task_type(
        task_types_dir,
        name="coding-task",
        schema={**VALID_SCHEMA, "title": "coding-task-updated"},
        policy=VALID_POLICY,
    )

    assert registry.list()[0].schema["title"] == "coding-task-updated"


def test_task_type_registration_endpoint_writes_and_lists_task_types(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    task_types_dir = tmp_path / "task_types"
    _write_task_type(
        task_types_dir, name="coding-task", schema=VALID_SCHEMA, policy=VALID_POLICY
    )

    registry = TaskTypeRegistry(task_types_dir)
    registry.load()

    app = create_app(session_factory=session_factory, task_type_registry=registry)
    admin_actor = seed_actor(
        session_factory,
        handle="task-type-admin",
        actor_type="admin",
        display_name="Task Type Admin",
    )
    admin_token = seed_token(
        session_factory,
        actor_id=admin_actor.id,
        scopes=["admin"],
    )
    agent_actor = seed_actor(
        session_factory,
        handle="task-type-agent",
        actor_type="agent",
        display_name="Task Type Agent",
    )
    agent_token = seed_token(
        session_factory,
        actor_id=agent_actor.id,
        scopes=["task:read"],
    )

    review_schema = {**VALID_SCHEMA, "title": "review-task"}
    review_policy = {"hitl_required": True, "autonomy_tier": 2}

    with TestClient(app) as client:
        initial_response = client.get("/task-types", headers=auth_headers(admin_token))
        assert initial_response.status_code == 200
        assert [item["name"] for item in initial_response.json()] == ["coding-task"]

        forbidden_response = client.post(
            "/task-types",
            headers=auth_headers(agent_token),
            json={
                "name": "review-task",
                "schema": review_schema,
                "policy": review_policy,
            },
        )
        assert forbidden_response.status_code == 403

        create_response = client.post(
            "/task-types",
            headers=auth_headers(admin_token),
            json={
                "name": "review-task",
                "schema": review_schema,
                "policy": review_policy,
            },
        )
        assert create_response.status_code == 201
        assert create_response.json()["name"] == "review-task"

        list_response = client.get(
            "/v1/task-types",
            headers=auth_headers(admin_token),
        )
        assert list_response.status_code == 200
        assert [item["name"] for item in list_response.json()] == [
            "coding-task",
            "review-task",
        ]

    assert (task_types_dir / "review-task.schema.json").exists()
    assert (task_types_dir / "review-task.policy.yaml").exists()


def test_task_type_registry_register_rejects_invalid_inputs(tmp_path: Path) -> None:
    task_types_dir = tmp_path / "task_types"
    _write_task_type(
        task_types_dir, name="coding-task", schema=VALID_SCHEMA, policy=VALID_POLICY
    )

    registry = TaskTypeRegistry(task_types_dir)
    registry.load()

    with pytest.raises(SchemaLoadError, match="Invalid task type name"):
        registry.register(
            name="Review Task",
            schema=VALID_SCHEMA,
            policy=VALID_POLICY,
        )

    with pytest.raises(SchemaLoadError, match=r"review-task\.schema\.json"):
        registry.register(
            name="review-task",
            schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": 7,
            },
            policy=VALID_POLICY,
        )

    with pytest.raises(SchemaLoadError, match="policy payload must be a mapping"):
        registry.register(
            name="review-task",
            schema=VALID_SCHEMA,
            policy=[],  # type: ignore[arg-type]
        )
