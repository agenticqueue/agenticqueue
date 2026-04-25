from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest
from alembic.config import Config

from agenticqueue_api import migrations


def test_migration_failure_prints_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_upgrade(config: Config, target: str) -> NoReturn:
        del config, target
        raise RuntimeError("column already exists")

    monkeypatch.setenv("AGENTICQUEUE_DATABASE_URL", "postgresql+asyncpg://original/db")
    monkeypatch.setattr(migrations, "upgrade", fail_upgrade)
    monkeypatch.setattr(migrations, "_current_revision", lambda: "20260423_28")
    monkeypatch.setattr(migrations, "_target_revision", lambda config: "20260424_29")
    monkeypatch.setattr(
        migrations,
        "_failing_migration_filename",
        lambda error: "20260424_29_users_email.py",
    )

    with pytest.raises(SystemExit):
        migrations.apply_database_migrations(tmp_path / "alembic.ini")

    stderr = capsys.readouterr().err
    assert "[MIGRATION_FAIL] Alembic migration failed during API startup." in stderr
    assert "[MIGRATION_FAIL] current_rev=20260423_28" in stderr
    assert "[MIGRATION_FAIL] target_rev=20260424_29" in stderr
    assert (
        "[MIGRATION_FAIL] failing_migration=20260424_29_users_email.py" in stderr
    )
    assert "[MIGRATION_FAIL] exception=RuntimeError: column already exists" in stderr
    assert "docs/local-dev-recovery.md" in stderr


def test_exits_one(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_upgrade(config: Config, target: str) -> NoReturn:
        del config, target
        raise RuntimeError("column already exists")

    monkeypatch.setattr(migrations, "upgrade", fail_upgrade)
    monkeypatch.setattr(migrations, "_current_revision", lambda: "20260423_28")
    monkeypatch.setattr(migrations, "_target_revision", lambda config: "20260424_29")
    monkeypatch.setattr(
        migrations,
        "_failing_migration_filename",
        lambda error: "20260424_29_users_email.py",
    )

    with pytest.raises(SystemExit) as exc_info:
        migrations.apply_database_migrations(tmp_path / "alembic.ini")

    assert exc_info.value.code == 1
