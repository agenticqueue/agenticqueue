"""Alembic migration helpers used by app startup and local tooling."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import TextIO

from alembic.command import upgrade
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
import sqlalchemy as sa

from agenticqueue_api.config import (
    get_alembic_config_path,
    get_direct_database_url,
    get_sync_database_url,
)

RECOVERY_HINT = (
    "see docs/local-dev-recovery.md; run "
    "`uv run alembic -c apps/api/alembic.ini current` and inspect the failing "
    "migration before retrying startup"
)


def apply_database_migrations(config_path: Path | None = None) -> None:
    """Bring the configured database to Alembic head."""

    config = Config(str(config_path or get_alembic_config_path()))
    original_database_url = os.environ.get("AGENTICQUEUE_DATABASE_URL")
    os.environ["AGENTICQUEUE_DATABASE_URL"] = get_direct_database_url()
    try:
        upgrade(config, "head")
    except Exception as error:
        _write_migration_failure_diagnostic(sys.stderr, config, error)
        sys.exit(1)
    finally:
        if original_database_url is None:
            os.environ.pop("AGENTICQUEUE_DATABASE_URL", None)
        else:
            os.environ["AGENTICQUEUE_DATABASE_URL"] = original_database_url


def _write_migration_failure_diagnostic(
    stream: TextIO,
    config: Config,
    error: Exception,
) -> None:
    stream.write(
        "\n".join(
            [
                "[MIGRATION_FAIL] Alembic migration failed during API startup.",
                f"[MIGRATION_FAIL] current_rev={_current_revision()}",
                f"[MIGRATION_FAIL] target_rev={_target_revision(config)}",
                f"[MIGRATION_FAIL] failing_migration={_failing_migration_filename(error)}",
                f"[MIGRATION_FAIL] exception={type(error).__name__}: {error}",
                f"[MIGRATION_FAIL] recovery_hint={RECOVERY_HINT}",
                "",
            ]
        )
    )
    stream.flush()


def _current_revision() -> str:
    try:
        engine = sa.create_engine(get_sync_database_url(), future=True)
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            heads = context.get_current_heads()
        engine.dispose()
    except Exception as error:
        return f"unavailable ({type(error).__name__}: {error})"
    if not heads:
        return "base"
    return ",".join(heads)


def _target_revision(config: Config) -> str:
    try:
        heads = ScriptDirectory.from_config(config).get_heads()
    except Exception as error:
        return f"head unavailable ({type(error).__name__}: {error})"
    return ",".join(heads) if heads else "head"


def _failing_migration_filename(error: Exception) -> str:
    for frame in traceback.extract_tb(error.__traceback__):
        filename = frame.filename.replace("\\", "/")
        if "/alembic/versions/" in filename:
            return filename.rsplit("/", maxsplit=1)[-1]
    return "unknown"
