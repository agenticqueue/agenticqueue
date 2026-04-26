from __future__ import annotations

# ruff: noqa: E402

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from test_support.db_isolation import (
    prepare_pytest_database,
    teardown_pytest_database,
)  # noqa: E402

_PREPARED_TEST_DATABASE = prepare_pytest_database()


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    del session, exitstatus
    teardown_pytest_database(_PREPARED_TEST_DATABASE)
