"""Alembic migration helpers used by app startup and local tooling."""

from __future__ import annotations

import os
from pathlib import Path

from alembic.command import upgrade
from alembic.config import Config

from agenticqueue_api.config import get_alembic_config_path, get_direct_database_url


def apply_database_migrations(config_path: Path | None = None) -> None:
    """Bring the configured database to Alembic head."""

    config = Config(str(config_path or get_alembic_config_path()))
    original_database_url = os.environ.get("AGENTICQUEUE_DATABASE_URL")
    os.environ["AGENTICQUEUE_DATABASE_URL"] = get_direct_database_url()
    try:
        upgrade(config, "head")
    finally:
        if original_database_url is None:
            os.environ.pop("AGENTICQUEUE_DATABASE_URL", None)
        else:
            os.environ["AGENTICQUEUE_DATABASE_URL"] = original_database_url
