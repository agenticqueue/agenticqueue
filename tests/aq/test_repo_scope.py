from __future__ import annotations

import math
from pathlib import Path

import pytest

from agenticqueue_api.repo_scope import resolve_repo_scope


def _write(repo_root: Path, relative_path: str, contents: str) -> Path:
    path = repo_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


def test_resolve_repo_scope_expands_globs_directories_and_exact_paths(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write(repo_root, "src/auth/login.py", "token-login\n")
    _write(repo_root, "src/auth/models.py", "token-models\n")
    _write(repo_root, "src/users/profile.py", "token-profile\n")

    resolved = resolve_repo_scope(
        repo_root,
        ["src/auth/**", "src/auth/login.py", "src/auth"],
        max_files=10,
    )

    assert resolved.file_scope == [
        "src/auth/login.py",
        "src/auth/models.py",
    ]
    expected_bytes = sum(
        (repo_root / path).stat().st_size for path in resolved.file_scope
    )
    assert resolved.estimated_token_count == math.ceil(expected_bytes / 4)


def test_resolve_repo_scope_rejects_oversized_expansions(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write(repo_root, "src/auth/login.py", "token-login\n")
    _write(repo_root, "src/auth/models.py", "token-models\n")

    with pytest.raises(ValueError, match="exceeds max_files=1"):
        resolve_repo_scope(repo_root, ["src/auth/**"], max_files=1)


@pytest.mark.parametrize(
    ("file_scope", "message"),
    [
        (["../secrets.txt"], "must stay inside the repo"),
        (["/tmp/secrets.txt"], "must be repo-relative"),
        (["missing.py"], "did not match any files"),
        (["src/auth/**"], "did not match any files"),
    ],
)
def test_resolve_repo_scope_rejects_invalid_entries(
    tmp_path: Path,
    file_scope: list[str],
    message: str,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write(repo_root, "src/real.py", "print('ok')\n")

    with pytest.raises(ValueError, match=message):
        resolve_repo_scope(repo_root, file_scope, max_files=10)


def test_resolve_repo_scope_handles_empty_scope_and_bad_roots(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    empty_scope = resolve_repo_scope(repo_root, [], max_files=10)
    assert empty_scope.file_scope == []
    assert empty_scope.estimated_token_count == 0

    with pytest.raises(ValueError, match="repo_root must be an existing directory"):
        resolve_repo_scope(repo_root / "missing", ["src/auth/**"], max_files=10)

    with pytest.raises(ValueError, match="max_files must be at least 1"):
        resolve_repo_scope(repo_root, ["src/auth/**"], max_files=0)
