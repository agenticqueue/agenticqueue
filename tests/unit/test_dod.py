from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from agenticqueue_api.config import get_task_types_dir
from agenticqueue_api.dod import run_dod_checks
from agenticqueue_api.dod_checks import VALID_CHECK_TYPES
from agenticqueue_api.dod_checks.artifact_size import run as run_artifact_size
from agenticqueue_api.dod_checks.ci_status import run as run_ci_status
from agenticqueue_api.dod_checks.common import (
    ArtifactBundle,
    DodCheckContext,
    DodCheckDefinition,
    DodCheckValidationError,
    GitHubClientProtocol,
    DodItemState,
    coerce_check_definition,
    select_artifacts,
)
from agenticqueue_api.dod_checks.grep_absent import run as run_grep_absent
from agenticqueue_api.dod_checks.grep_present import run as run_grep_present
from agenticqueue_api.dod_checks.path_absent import run as run_path_absent
from agenticqueue_api.dod_checks.path_exists import run as run_path_exists
from agenticqueue_api.dod_checks.pr_mergeable import run as run_pr_mergeable
from agenticqueue_api.dod_checks.schema_validates import run as run_schema_validates
from agenticqueue_api.dod_checks.test_count import run as run_test_count
from agenticqueue_api.models.task import TaskModel
from agenticqueue_api.task_type_registry import TaskTypeRegistry


def _registry() -> TaskTypeRegistry:
    registry = TaskTypeRegistry(get_task_types_dir())
    registry.load()
    return registry


def _task(*, dod_checks: list[dict[str, object]], definition_of_done: list[str]) -> TaskModel:
    return TaskModel.model_validate(
        {
            "id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "task_type": "coding-task",
            "title": "DoD test task",
            "state": "queued",
            "description": "Task payload used to validate DoD runners",
            "contract": {"dod_checks": dod_checks},
            "definition_of_done": definition_of_done,
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )


def _write(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def _bundle(tmp_path: Path, *, include_patch: bool = True) -> ArtifactBundle:
    diff_path = tmp_path / "artifacts" / "diffs" / "aq-55.patch"
    if include_patch:
        _write(diff_path, "@@ /v1/tasks/{id}\n+ route\n")
    report_path = tmp_path / "artifacts" / "tests" / "junit.xml"
    _write(
        report_path,
        "<testsuite>"
        "<testcase classname='unit' name='a'/>"
        "<testcase classname='unit' name='b'/>"
        "</testsuite>",
    )
    json_path = tmp_path / "artifacts" / "contracts" / "coding-task.json"
    _write(
        json_path,
        json.dumps(
            {
                "repo": "github.com/agenticqueue/agenticqueue",
                "branch": "main",
                "file_scope": ["apps/api/src/agenticqueue_api/app.py"],
                "surface_area": ["contract-engine"],
                "spec": "Goal",
                "dod_checklist": ["one"],
                "autonomy_tier": 3,
                "output": {
                    "diff_url": "patch.diff",
                    "test_report": "report.txt",
                    "artifacts": [{"kind": "patch", "uri": "patch.diff"}],
                    "learnings": [],
                },
            }
        ),
    )

    output = {
        "artifacts": [
            {"kind": "patch", "uri": str(diff_path), "details": {}},
            {"kind": "test-report", "uri": str(report_path), "details": {}},
            {"kind": "json", "uri": str(json_path), "details": {}},
        ],
        "test_report": str(report_path),
    }
    return ArtifactBundle.from_output(output)


def _submission_output(bundle: ArtifactBundle) -> dict[str, object]:
    return {
        "artifacts": [
            {"kind": "artifact", "uri": artifact.uri, "details": artifact.details}
            for artifact in bundle.files.values()
        ],
        "test_report": next(
            artifact.uri
            for artifact in bundle.files.values()
            if artifact.uri.endswith("junit.xml")
        ),
    }


def _context(
    tmp_path: Path,
    *,
    github_client: GitHubClientProtocol | None = None,
    include_patch: bool = True,
) -> DodCheckContext:
    return DodCheckContext(
        bundle=_bundle(tmp_path, include_patch=include_patch),
        registry=_registry(),
        github_client=github_client,
    )


class FakeGitHubClient:
    def __init__(
        self,
        *,
        check_conclusion: str | None = None,
        mergeable: bool | None = None,
        raise_error: bool = False,
    ) -> None:
        self.check_conclusion = check_conclusion
        self.mergeable = mergeable
        self.raise_error = raise_error

    def get_check_conclusion(
        self,
        *,
        repo: str,
        sha: str,
        check_name: str,
        timeout_seconds: float,
    ) -> str | None:
        assert repo
        assert sha
        assert check_name
        assert timeout_seconds > 0
        if self.raise_error:
            raise RuntimeError("boom")
        return self.check_conclusion

    def get_pull_request_mergeable(
        self,
        *,
        repo: str,
        pr_number: int,
        timeout_seconds: float,
    ) -> bool | None:
        assert repo
        assert pr_number > 0
        assert timeout_seconds > 0
        if self.raise_error:
            raise RuntimeError("boom")
        return self.mergeable


def test_run_dod_checks_matches_ticket_fixture(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    task = _task(
        dod_checks=[
            {
                "item": "path exists",
                "type": "path_exists",
                "path": str(tmp_path / "artifacts" / "diffs" / "aq-55.patch"),
            },
            {
                "item": "schema validates",
                "type": "schema_validates",
                "path": str(tmp_path / "artifacts" / "contracts" / "coding-task.json"),
                "schema_name": "coding-task",
            },
            {
                "item": "grep present",
                "type": "grep_present",
                "path": str(tmp_path / "artifacts" / "diffs" / "aq-55.patch"),
                "pattern": "/v1/tasks",
            },
            {
                "item": "test count",
                "type": "test_count",
                "path": str(tmp_path / "artifacts" / "tests" / "junit.xml"),
                "min_count": 3,
            },
        ],
        definition_of_done=[
            "path exists",
            "schema validates",
            "grep present",
            "test count",
        ],
    )

    report = run_dod_checks(
        task,
        _submission_output(bundle),
        registry=_registry(),
    )

    assert [item.state for item in report.checklist] == [
        DodItemState.CHECKED,
        DodItemState.CHECKED,
        DodItemState.CHECKED,
        DodItemState.UNCHECKED_UNMET,
    ]
    assert report.checked_count == 3
    assert report.unchecked_unmet_count == 1


def test_run_dod_checks_supports_partial_and_blocked_items(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    missing_uri = str(tmp_path / "artifacts" / "diffs" / "missing.patch")
    task = _task(
        dod_checks=[
            {
                "item": "partial item",
                "type": "path_exists",
                "path": next(iter(bundle.files)),
            },
            {
                "item": "partial item",
                "type": "grep_present",
                "path": next(iter(bundle.files)),
                "pattern": "missing",
            },
            {
                "item": "blocked item",
                "type": "ci_status",
                "repo": "agenticqueue/agenticqueue",
                "sha": "abc123",
                "check_name": "test",
            },
            {
                "item": "extra appended item",
                "type": "path_absent",
                "path": missing_uri,
            },
        ],
        definition_of_done=["partial item", "blocked item", "no checks item"],
    )

    report = run_dod_checks(
        task,
        _submission_output(bundle),
        registry=_registry(),
    )

    assert [item.state for item in report.checklist] == [
        DodItemState.PARTIAL,
        DodItemState.UNCHECKED_BLOCKED,
        DodItemState.UNCHECKED_BLOCKED,
        DodItemState.CHECKED,
    ]
    assert report.partial_count == 1
    assert report.unchecked_blocked_count == 2
    assert report.checked_count == 1


@pytest.mark.parametrize(
    ("raw_checks", "expected"),
    [
        (None, "Task contract must declare a non-empty 'dod_checks' list."),
        ([{"item": "x", "type": "shell", "cmd": "pytest"}], "shell exec disabled; see ADR-AQ-012"),
        ([{"item": "x", "type": "wat"}], "Unknown DoD check type 'wat'. Valid types: " + ", ".join(VALID_CHECK_TYPES) + "."),
    ],
)
def test_run_dod_checks_rejects_invalid_contracts(
    tmp_path: Path,
    raw_checks: object,
    expected: str,
) -> None:
    contract: dict[str, object] = {}
    if raw_checks is not None:
        contract["dod_checks"] = raw_checks
    task = TaskModel.model_validate(
        {
            "id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "task_type": "coding-task",
            "title": "Invalid",
            "state": "queued",
            "description": "Invalid",
            "contract": contract,
            "definition_of_done": ["x"],
            "created_at": "2026-04-20T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        }
    )

    with pytest.raises(DodCheckValidationError, match=expected):
        run_dod_checks(task, {"artifacts": []}, registry=_registry())


def test_common_helpers_cover_bundle_and_selection_errors(tmp_path: Path) -> None:
    with pytest.raises(
        DodCheckValidationError,
        match="Submission output must include an 'artifacts' list",
    ):
        ArtifactBundle.from_output({})

    with pytest.raises(DodCheckValidationError, match="Artifact entry 0 must be an object"):
        ArtifactBundle.from_output({"artifacts": ["nope"]})

    with pytest.raises(
        DodCheckValidationError,
        match="Artifact entry 0 must declare a non-empty uri",
    ):
        ArtifactBundle.from_output({"artifacts": [{"kind": "patch"}]})

    definition = coerce_check_definition({"item": "x", "type": "path_exists"})
    assert definition.timeout_seconds == 30.0
    with pytest.raises(
        DodCheckValidationError,
        match="positive timeout_seconds",
    ):
        coerce_check_definition({"item": "x", "type": "path_exists", "timeout_seconds": 0})

    with pytest.raises(DodCheckValidationError, match="Unsupported path_mode 'bad'"):
        select_artifacts(_bundle(tmp_path), path_expr="*", path_mode="bad")


def test_path_and_grep_handlers_cover_absence_modes_and_invalid_regex(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    patch_uri = str(tmp_path / "artifacts" / "diffs" / "aq-55.patch")
    missing_uri = str(tmp_path / "artifacts" / "diffs" / "missing.patch")

    assert run_path_exists(
        DodCheckDefinition(
            item="exists",
            check_type="path_exists",
            fields={"item": "exists", "type": "path_exists", "path": patch_uri},
            timeout_seconds=30,
        ),
        context,
    ).state == DodItemState.CHECKED

    assert run_path_exists(
        DodCheckDefinition(
            item="exists",
            check_type="path_exists",
            fields={
                "item": "exists",
                "type": "path_exists",
                "path": str(tmp_path / "artifacts" / "diffs" / "*.patch"),
                "path_mode": "glob",
            },
            timeout_seconds=30,
        ),
        context,
    ).state == DodItemState.CHECKED

    assert run_path_exists(
        DodCheckDefinition(
            item="missing",
            check_type="path_exists",
            fields={"item": "missing", "type": "path_exists", "path": missing_uri},
            timeout_seconds=30,
        ),
        context,
    ).state == DodItemState.UNCHECKED_UNMET

    assert run_path_absent(
        DodCheckDefinition(
            item="absent",
            check_type="path_absent",
            fields={"item": "absent", "type": "path_absent", "path": missing_uri},
            timeout_seconds=30,
        ),
        context,
    ).state == DodItemState.CHECKED

    assert run_path_absent(
        DodCheckDefinition(
            item="absent",
            check_type="path_absent",
            fields={"item": "absent", "type": "path_absent", "path": patch_uri},
            timeout_seconds=30,
        ),
        context,
    ).state == DodItemState.UNCHECKED_UNMET

    assert run_grep_present(
        DodCheckDefinition(
            item="grep",
            check_type="grep_present",
            fields={
                "item": "grep",
                "type": "grep_present",
                "path": patch_uri,
                "pattern": "/v1/tasks",
            },
            timeout_seconds=30,
        ),
        context,
    ).state == DodItemState.CHECKED

    assert run_grep_present(
        DodCheckDefinition(
            item="grep",
            check_type="grep_present",
            fields={
                "item": "grep",
                "type": "grep_present",
                "path": patch_uri,
                "pattern": "missing",
            },
            timeout_seconds=30,
        ),
        context,
    ).state == DodItemState.UNCHECKED_UNMET

    assert run_grep_absent(
        DodCheckDefinition(
            item="grep",
            check_type="grep_absent",
            fields={
                "item": "grep",
                "type": "grep_absent",
                "path": patch_uri,
                "pattern": "missing",
            },
            timeout_seconds=30,
        ),
        context,
    ).state == DodItemState.CHECKED

    assert run_grep_absent(
        DodCheckDefinition(
            item="grep",
            check_type="grep_absent",
            fields={
                "item": "grep",
                "type": "grep_absent",
                "path": patch_uri,
                "pattern": "/v1/tasks",
            },
            timeout_seconds=30,
        ),
        context,
    ).state == DodItemState.UNCHECKED_UNMET

    with pytest.raises(DodCheckValidationError, match="Invalid grep regex"):
        run_grep_present(
            DodCheckDefinition(
                item="bad",
                check_type="grep_present",
                fields={
                    "item": "bad",
                    "type": "grep_present",
                    "path": patch_uri,
                    "pattern": "(",
                },
                timeout_seconds=30,
            ),
            context,
        )


def test_schema_test_count_and_artifact_size_handlers_cover_edge_cases(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    json_uri = str(tmp_path / "artifacts" / "contracts" / "coding-task.json")
    report_uri = str(tmp_path / "artifacts" / "tests" / "junit.xml")
    patch_uri = str(tmp_path / "artifacts" / "diffs" / "aq-55.patch")

    assert run_schema_validates(
        DodCheckDefinition(
            item="schema",
            check_type="schema_validates",
            fields={
                "item": "schema",
                "type": "schema_validates",
                "path": json_uri,
                "schema_name": "coding-task",
            },
            timeout_seconds=30,
        ),
        context,
    ).state == DodItemState.CHECKED

    bad_json = tmp_path / "artifacts" / "contracts" / "bad.json"
    _write(bad_json, "{not-json")
    bad_bundle = ArtifactBundle.from_output(
        {"artifacts": [{"kind": "json", "uri": str(bad_json), "details": {}}]}
    )
    bad_context = DodCheckContext(bundle=bad_bundle, registry=_registry())
    assert run_schema_validates(
        DodCheckDefinition(
            item="schema",
            check_type="schema_validates",
            fields={
                "item": "schema",
                "type": "schema_validates",
                "path": str(bad_json),
                "schema_name": "coding-task",
            },
            timeout_seconds=30,
        ),
        bad_context,
    ).state == DodItemState.UNCHECKED_UNMET

    with pytest.raises(DodCheckValidationError, match="Unknown task type"):
        run_schema_validates(
            DodCheckDefinition(
                item="schema",
                check_type="schema_validates",
                fields={
                    "item": "schema",
                    "type": "schema_validates",
                    "path": json_uri,
                    "schema_name": "missing-task-type",
                },
                timeout_seconds=30,
            ),
            context,
        )

    assert run_test_count(
        DodCheckDefinition(
            item="tests",
            check_type="test_count",
            fields={
                "item": "tests",
                "type": "test_count",
                "path": report_uri,
                "min_count": 2,
            },
            timeout_seconds=30,
        ),
        context,
    ).state == DodItemState.CHECKED

    assert run_test_count(
        DodCheckDefinition(
            item="tests",
            check_type="test_count",
            fields={
                "item": "tests",
                "type": "test_count",
                "path": report_uri,
                "min_count": 3,
            },
            timeout_seconds=30,
        ),
        context,
    ).state == DodItemState.UNCHECKED_UNMET

    broken_xml = tmp_path / "artifacts" / "tests" / "broken.xml"
    _write(broken_xml, "<testsuite>")
    broken_context = DodCheckContext(
        bundle=ArtifactBundle.from_output(
            {"artifacts": [{"kind": "test-report", "uri": str(broken_xml), "details": {}}]}
        ),
        registry=_registry(),
    )
    assert run_test_count(
        DodCheckDefinition(
            item="tests",
            check_type="test_count",
            fields={
                "item": "tests",
                "type": "test_count",
                "path": str(broken_xml),
                "min_count": 1,
            },
            timeout_seconds=30,
        ),
        broken_context,
    ).state == DodItemState.UNCHECKED_UNMET

    assert run_artifact_size(
        DodCheckDefinition(
            item="size",
            check_type="artifact_size",
            fields={
                "item": "size",
                "type": "artifact_size",
                "path": patch_uri,
                "min_bytes": 1,
                "max_bytes": 200,
            },
            timeout_seconds=30,
        ),
        context,
    ).state == DodItemState.CHECKED

    assert run_artifact_size(
        DodCheckDefinition(
            item="size",
            check_type="artifact_size",
            fields={
                "item": "size",
                "type": "artifact_size",
                "path": patch_uri,
                "min_bytes": 500,
            },
            timeout_seconds=30,
        ),
        context,
    ).state == DodItemState.UNCHECKED_UNMET

    with pytest.raises(DodCheckValidationError, match="requires 'min_bytes' and/or 'max_bytes'"):
        run_artifact_size(
            DodCheckDefinition(
                item="size",
                check_type="artifact_size",
                fields={"item": "size", "type": "artifact_size", "path": patch_uri},
                timeout_seconds=30,
            ),
            context,
        )

    with pytest.raises(DodCheckValidationError, match="min_bytes <= max_bytes"):
        run_artifact_size(
            DodCheckDefinition(
                item="size",
                check_type="artifact_size",
                fields={
                    "item": "size",
                    "type": "artifact_size",
                    "path": patch_uri,
                    "min_bytes": 10,
                    "max_bytes": 1,
                },
                timeout_seconds=30,
            ),
            context,
        )


def test_handlers_cover_missing_artifact_and_empty_bundle_paths(tmp_path: Path) -> None:
    missing_uri = str(tmp_path / "artifacts" / "diffs" / "missing.patch")
    empty_context = DodCheckContext(
        bundle=ArtifactBundle.from_output({"artifacts": []}),
        registry=_registry(),
    )

    assert run_grep_present(
        DodCheckDefinition(
            item="grep",
            check_type="grep_present",
            fields={
                "item": "grep",
                "type": "grep_present",
                "path": missing_uri,
                "pattern": "anything",
            },
            timeout_seconds=30,
        ),
        empty_context,
    ).state == DodItemState.UNCHECKED_UNMET

    assert run_grep_absent(
        DodCheckDefinition(
            item="grep",
            check_type="grep_absent",
            fields={
                "item": "grep",
                "type": "grep_absent",
                "path": missing_uri,
                "pattern": "anything",
            },
            timeout_seconds=30,
        ),
        empty_context,
    ).state == DodItemState.CHECKED

    assert run_schema_validates(
        DodCheckDefinition(
            item="schema",
            check_type="schema_validates",
            fields={
                "item": "schema",
                "type": "schema_validates",
                "path": missing_uri,
                "schema_name": "coding-task",
            },
            timeout_seconds=30,
        ),
        empty_context,
    ).state == DodItemState.UNCHECKED_UNMET

    assert run_test_count(
        DodCheckDefinition(
            item="tests",
            check_type="test_count",
            fields={
                "item": "tests",
                "type": "test_count",
                "path": missing_uri,
                "min_count": 1,
            },
            timeout_seconds=30,
        ),
        empty_context,
    ).state == DodItemState.UNCHECKED_UNMET

    declared_missing_context = DodCheckContext(
        bundle=ArtifactBundle.from_output(
            {
                "artifacts": [
                    {"kind": "patch", "uri": missing_uri, "details": {}},
                ]
            }
        ),
        registry=_registry(),
    )

    assert run_path_exists(
        DodCheckDefinition(
            item="exists",
            check_type="path_exists",
            fields={"item": "exists", "type": "path_exists", "path": missing_uri},
            timeout_seconds=30,
        ),
        declared_missing_context,
    ).state == DodItemState.UNCHECKED_UNMET

    assert run_grep_present(
        DodCheckDefinition(
            item="grep",
            check_type="grep_present",
            fields={
                "item": "grep",
                "type": "grep_present",
                "path": missing_uri,
                "pattern": "anything",
            },
            timeout_seconds=30,
        ),
        declared_missing_context,
    ).state == DodItemState.UNCHECKED_UNMET

    assert run_grep_absent(
        DodCheckDefinition(
            item="grep",
            check_type="grep_absent",
            fields={
                "item": "grep",
                "type": "grep_absent",
                "path": missing_uri,
                "pattern": "anything",
            },
            timeout_seconds=30,
        ),
        declared_missing_context,
    ).state == DodItemState.CHECKED

    assert run_schema_validates(
        DodCheckDefinition(
            item="schema",
            check_type="schema_validates",
            fields={
                "item": "schema",
                "type": "schema_validates",
                "path": missing_uri,
                "schema_name": "coding-task",
            },
            timeout_seconds=30,
        ),
        declared_missing_context,
    ).state == DodItemState.UNCHECKED_UNMET

    assert run_test_count(
        DodCheckDefinition(
            item="tests",
            check_type="test_count",
            fields={
                "item": "tests",
                "type": "test_count",
                "path": missing_uri,
                "min_count": 1,
            },
            timeout_seconds=30,
        ),
        declared_missing_context,
    ).state == DodItemState.UNCHECKED_UNMET

    assert run_artifact_size(
        DodCheckDefinition(
            item="size",
            check_type="artifact_size",
            fields={
                "item": "size",
                "type": "artifact_size",
                "path": missing_uri,
                "min_bytes": 1,
            },
            timeout_seconds=30,
        ),
        empty_context,
    ).state == DodItemState.UNCHECKED_UNMET

    assert run_artifact_size(
        DodCheckDefinition(
            item="size",
            check_type="artifact_size",
            fields={
                "item": "size",
                "type": "artifact_size",
                "path": missing_uri,
                "min_bytes": 1,
            },
            timeout_seconds=30,
        ),
        declared_missing_context,
    ).state == DodItemState.UNCHECKED_UNMET


def test_handlers_cover_remaining_error_branches(tmp_path: Path) -> None:
    context = _context(tmp_path)
    patch_uri = str(tmp_path / "artifacts" / "diffs" / "aq-55.patch")
    json_uri = str(tmp_path / "artifacts" / "contracts" / "coding-task.json")

    with pytest.raises(DodCheckValidationError, match="Invalid grep regex"):
        run_grep_absent(
            DodCheckDefinition(
                item="grep",
                check_type="grep_absent",
                fields={
                    "item": "grep",
                    "type": "grep_absent",
                    "path": patch_uri,
                    "pattern": "(",
                },
                timeout_seconds=30,
            ),
            context,
        )

    invalid_schema = tmp_path / "artifacts" / "contracts" / "invalid-schema.json"
    _write(invalid_schema, json.dumps({"repo": "missing required fields"}))
    invalid_schema_context = DodCheckContext(
        bundle=ArtifactBundle.from_output(
            {"artifacts": [{"kind": "json", "uri": str(invalid_schema), "details": {}}]}
        ),
        registry=_registry(),
    )
    assert run_schema_validates(
        DodCheckDefinition(
            item="schema",
            check_type="schema_validates",
            fields={
                "item": "schema",
                "type": "schema_validates",
                "path": str(invalid_schema),
                "schema_name": "coding-task",
            },
            timeout_seconds=30,
        ),
        invalid_schema_context,
    ).state == DodItemState.UNCHECKED_UNMET

    assert run_artifact_size(
        DodCheckDefinition(
            item="size",
            check_type="artifact_size",
            fields={
                "item": "size",
                "type": "artifact_size",
                "path": patch_uri,
                "max_bytes": 1,
            },
            timeout_seconds=30,
        ),
        context,
    ).state == DodItemState.UNCHECKED_UNMET


def test_github_handlers_cover_success_failure_and_blocked() -> None:
    context_success = DodCheckContext(
        bundle=ArtifactBundle(files={}),
        registry=_registry(),
        github_client=FakeGitHubClient(check_conclusion="success", mergeable=True),
    )
    assert run_ci_status(
        DodCheckDefinition(
            item="ci",
            check_type="ci_status",
            fields={
                "item": "ci",
                "type": "ci_status",
                "repo": "agenticqueue/agenticqueue",
                "sha": "abc123",
                "check_name": "build",
            },
            timeout_seconds=30,
        ),
        context_success,
    ).state == DodItemState.CHECKED
    assert run_pr_mergeable(
        DodCheckDefinition(
            item="pr",
            check_type="pr_mergeable",
            fields={
                "item": "pr",
                "type": "pr_mergeable",
                "repo": "agenticqueue/agenticqueue",
                "pr_number": 5,
            },
            timeout_seconds=30,
        ),
        context_success,
    ).state == DodItemState.CHECKED

    context_failure = DodCheckContext(
        bundle=ArtifactBundle(files={}),
        registry=_registry(),
        github_client=FakeGitHubClient(check_conclusion="failure", mergeable=False),
    )
    assert run_ci_status(
        DodCheckDefinition(
            item="ci",
            check_type="ci_status",
            fields={
                "item": "ci",
                "type": "ci_status",
                "repo": "agenticqueue/agenticqueue",
                "sha": "abc123",
                "check_name": "build",
            },
            timeout_seconds=30,
        ),
        context_failure,
    ).state == DodItemState.UNCHECKED_UNMET
    assert run_pr_mergeable(
        DodCheckDefinition(
            item="pr",
            check_type="pr_mergeable",
            fields={
                "item": "pr",
                "type": "pr_mergeable",
                "repo": "agenticqueue/agenticqueue",
                "pr_number": 5,
            },
            timeout_seconds=30,
        ),
        context_failure,
    ).state == DodItemState.UNCHECKED_UNMET

    context_pending = DodCheckContext(
        bundle=ArtifactBundle(files={}),
        registry=_registry(),
        github_client=FakeGitHubClient(check_conclusion=None, mergeable=None),
    )
    assert run_ci_status(
        DodCheckDefinition(
            item="ci",
            check_type="ci_status",
            fields={
                "item": "ci",
                "type": "ci_status",
                "repo": "agenticqueue/agenticqueue",
                "sha": "abc123",
                "check_name": "build",
            },
            timeout_seconds=30,
        ),
        context_pending,
    ).state == DodItemState.UNCHECKED_BLOCKED
    assert run_pr_mergeable(
        DodCheckDefinition(
            item="pr",
            check_type="pr_mergeable",
            fields={
                "item": "pr",
                "type": "pr_mergeable",
                "repo": "agenticqueue/agenticqueue",
                "pr_number": 5,
            },
            timeout_seconds=30,
        ),
        context_pending,
    ).state == DodItemState.UNCHECKED_BLOCKED

    context_blocked = DodCheckContext(
        bundle=ArtifactBundle(files={}),
        registry=_registry(),
        github_client=None,
    )
    assert run_ci_status(
        DodCheckDefinition(
            item="ci",
            check_type="ci_status",
            fields={
                "item": "ci",
                "type": "ci_status",
                "repo": "agenticqueue/agenticqueue",
                "sha": "abc123",
                "check_name": "build",
            },
            timeout_seconds=30,
        ),
        context_blocked,
    ).state == DodItemState.UNCHECKED_BLOCKED
    assert run_pr_mergeable(
        DodCheckDefinition(
            item="pr",
            check_type="pr_mergeable",
            fields={
                "item": "pr",
                "type": "pr_mergeable",
                "repo": "agenticqueue/agenticqueue",
                "pr_number": 5,
            },
            timeout_seconds=30,
        ),
        context_blocked,
    ).state == DodItemState.UNCHECKED_BLOCKED

    context_error = DodCheckContext(
        bundle=ArtifactBundle(files={}),
        registry=_registry(),
        github_client=FakeGitHubClient(raise_error=True),
    )
    assert run_ci_status(
        DodCheckDefinition(
            item="ci",
            check_type="ci_status",
            fields={
                "item": "ci",
                "type": "ci_status",
                "repo": "agenticqueue/agenticqueue",
                "sha": "abc123",
                "check_name": "build",
            },
            timeout_seconds=30,
        ),
        context_error,
    ).state == DodItemState.UNCHECKED_BLOCKED
    assert run_pr_mergeable(
        DodCheckDefinition(
            item="pr",
            check_type="pr_mergeable",
            fields={
                "item": "pr",
                "type": "pr_mergeable",
                "repo": "agenticqueue/agenticqueue",
                "pr_number": 5,
            },
            timeout_seconds=30,
        ),
        context_error,
    ).state == DodItemState.UNCHECKED_BLOCKED
