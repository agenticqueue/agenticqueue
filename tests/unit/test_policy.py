from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agenticqueue_api.capability_keys import CapabilityKey
from agenticqueue_api.policy import (
    PolicyLoadError,
    PolicyPack,
    PolicyRegistry,
    load_policy_pack,
    resolve_effective_policy,
)


def _write_policy(
    directory: Path,
    *,
    name: str,
    body: str,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.policy.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _policy_pack(
    *,
    name: str,
    version: str,
    hitl_required: bool,
    autonomy_tier: int,
) -> PolicyPack:
    return PolicyPack(
        name=name,
        version=version,
        hitl_required=hitl_required,
        autonomy_tier=autonomy_tier,
        capabilities=(CapabilityKey.READ_REPO,),
        body={},
        path=Path(f"{name}.policy.yaml"),
    )


def test_policy_registry_loads_default_coding_pack_with_hitl_enabled(
    tmp_path: Path,
) -> None:
    policies_dir = tmp_path / "policies"
    _write_policy(
        policies_dir,
        name="default-coding",
        body=(
            'version: "1.0.0"\n'
            "hitl_required: true\n"
            "autonomy_tier: 3\n"
            "capabilities:\n"
            "  - read_repo\n"
            "  - read_repo\n"
        ),
    )

    registry = PolicyRegistry(policies_dir)
    registry.load()

    policy = registry.get("default-coding")
    assert policy.hitl_required is True
    assert policy.autonomy_tier == 3
    assert policy.capabilities == (CapabilityKey.READ_REPO,)
    assert [pack.name for pack in registry.list()] == ["default-coding"]


def test_task_policy_overrides_workspace_default_and_flips_validation_mode() -> None:
    default_policy = _policy_pack(
        name="default-coding",
        version="1.0.0",
        hitl_required=True,
        autonomy_tier=3,
    )
    task_policy = _policy_pack(
        name="task-override",
        version="1.0.1",
        hitl_required=False,
        autonomy_tier=2,
    )

    resolved = resolve_effective_policy(
        default_policy=default_policy,
        workspace_policy=default_policy,
        task_policy=task_policy,
    )

    assert resolved.source == "task"
    assert resolved.hitl_required is False
    assert resolved.validation_mode == "autonomous"


def test_project_policy_beats_workspace_until_task_override_exists() -> None:
    default_policy = _policy_pack(
        name="default-coding",
        version="1.0.0",
        hitl_required=False,
        autonomy_tier=2,
    )
    workspace_policy = _policy_pack(
        name="workspace-default",
        version="1.0.0",
        hitl_required=False,
        autonomy_tier=2,
    )
    project_policy = _policy_pack(
        name="project-override",
        version="2.0.0",
        hitl_required=True,
        autonomy_tier=3,
    )
    task_policy = _policy_pack(
        name="task-override",
        version="2.0.1",
        hitl_required=False,
        autonomy_tier=1,
    )

    project_resolved = resolve_effective_policy(
        default_policy=default_policy,
        workspace_policy=workspace_policy,
        project_policy=project_policy,
    )
    task_resolved = resolve_effective_policy(
        default_policy=default_policy,
        workspace_policy=workspace_policy,
        project_policy=project_policy,
        task_policy=task_policy,
    )
    default_resolved = resolve_effective_policy(default_policy=default_policy)

    assert project_resolved.source == "project"
    assert project_resolved.hitl_required is True
    assert project_resolved.validation_mode == "human_review"
    assert task_resolved.source == "task"
    assert task_resolved.hitl_required is False
    assert default_resolved.source == "default"


def test_workspace_policy_applies_when_no_higher_precedence_exists() -> None:
    default_policy = _policy_pack(
        name="default-coding",
        version="1.0.0",
        hitl_required=False,
        autonomy_tier=2,
    )
    workspace_policy = _policy_pack(
        name="workspace-default",
        version="1.0.1",
        hitl_required=True,
        autonomy_tier=3,
    )

    resolved = resolve_effective_policy(
        default_policy=default_policy,
        workspace_policy=workspace_policy,
    )

    assert resolved.source == "workspace"
    assert resolved.validation_mode == "human_review"


def test_policy_registry_rejects_missing_version_and_unknown_pack(
    tmp_path: Path,
) -> None:
    policies_dir = tmp_path / "policies"
    _write_policy(
        policies_dir,
        name="default-coding",
        body="hitl_required: true\nautonomy_tier: 3\n",
    )

    registry = PolicyRegistry(policies_dir)
    with pytest.raises(ValidationError):
        registry.load()

    with pytest.raises(PolicyLoadError, match="Unknown policy pack"):
        registry.get("missing-policy")


def test_policy_registry_rejects_out_of_range_tiers_and_bad_files(
    tmp_path: Path,
) -> None:
    with pytest.raises(PolicyLoadError, match="Policy directory not found"):
        PolicyRegistry(tmp_path / "missing").load()

    no_files_dir = tmp_path / "no-files"
    no_files_dir.mkdir()
    with pytest.raises(PolicyLoadError, match="No policy files found"):
        PolicyRegistry(no_files_dir).load()

    invalid_yaml_dir = tmp_path / "invalid-yaml"
    _write_policy(invalid_yaml_dir, name="default-coding", body="version: [1.0.0\n")
    with pytest.raises(PolicyLoadError, match="Invalid policy file"):
        PolicyRegistry(invalid_yaml_dir).load()

    not_mapping_dir = tmp_path / "not-mapping"
    _write_policy(
        not_mapping_dir,
        name="default-coding",
        body="- hitl_required\n- autonomy_tier\n",
    )
    with pytest.raises(PolicyLoadError, match="must contain a YAML mapping"):
        PolicyRegistry(not_mapping_dir).load()

    bad_tier_dir = tmp_path / "bad-tier"
    _write_policy(
        bad_tier_dir,
        name="default-coding",
        body=(
            'version: "1.0.0"\n'
            "hitl_required: true\n"
            "autonomy_tier: 7\n"
        ),
    )
    with pytest.raises(ValidationError):
        PolicyRegistry(bad_tier_dir).load()


def test_policy_loader_covers_blank_version_invalid_name_and_body_shapes(
    tmp_path: Path,
) -> None:
    blank_version_dir = tmp_path / "blank-version"
    _write_policy(
        blank_version_dir,
        name="default-coding",
        body=(
            'version: "   "\n'
            "hitl_required: true\n"
            "autonomy_tier: 3\n"
        ),
    )
    with pytest.raises(ValidationError):
        PolicyRegistry(blank_version_dir).load()

    empty_file_dir = tmp_path / "empty-file"
    _write_policy(empty_file_dir, name="default-coding", body="")
    with pytest.raises(ValidationError):
        PolicyRegistry(empty_file_dir).load()

    body_none_dir = tmp_path / "body-none"
    _write_policy(
        body_none_dir,
        name="default-coding",
        body=(
            'version: "1.0.0"\n'
            "hitl_required: true\n"
            "autonomy_tier: 3\n"
            "body:\n"
        ),
    )
    body_none_registry = PolicyRegistry(body_none_dir)
    body_none_registry.load()
    assert body_none_registry.get("default-coding").body == {}

    body_dict_dir = tmp_path / "body-dict"
    _write_policy(
        body_dict_dir,
        name="default-coding",
        body=(
            'version: "1.0.0"\n'
            "hitl_required: true\n"
            "autonomy_tier: 3\n"
            "body:\n"
            "  rule: keep\n"
        ),
    )
    body_dict_registry = PolicyRegistry(body_dict_dir)
    body_dict_registry.load()
    assert body_dict_registry.get("default-coding").body == {"rule": "keep"}

    body_list_dir = tmp_path / "body-list"
    _write_policy(
        body_list_dir,
        name="default-coding",
        body=(
            'version: "1.0.0"\n'
            "hitl_required: true\n"
            "autonomy_tier: 3\n"
            "body:\n"
            "  - invalid\n"
        ),
    )
    with pytest.raises(ValidationError):
        PolicyRegistry(body_list_dir).load()

    invalid_name_path = _write_policy(
        tmp_path / "invalid-name",
        name="Default_Coding",
        body=(
            'version: "1.0.0"\n'
            "hitl_required: true\n"
            "autonomy_tier: 3\n"
        ),
    )
    with pytest.raises(PolicyLoadError, match="Invalid policy file name"):
        load_policy_pack(invalid_name_path)
