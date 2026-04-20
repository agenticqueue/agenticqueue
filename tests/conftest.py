from __future__ import annotations

from typing import Any, cast

import psycopg
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from agenticqueue_api.config import get_psycopg_connect_args

_ORIGINAL_CREATE_ENGINE = sa.create_engine
_ORIGINAL_PSYCOPG_CONNECT = psycopg.connect


def _patched_create_engine(url: Any, *args: Any, **kwargs: Any) -> Engine:
    if isinstance(url, str) and url.startswith("postgresql+psycopg://"):
        connect_args = dict(cast(dict[str, Any], kwargs.get("connect_args") or {}))
        connect_args.setdefault(
            "prepare_threshold",
            get_psycopg_connect_args()["prepare_threshold"],
        )
        kwargs["connect_args"] = connect_args
    return _ORIGINAL_CREATE_ENGINE(url, *args, **kwargs)


def _patched_psycopg_connect(conninfo: str = "", *args: Any, **kwargs: Any) -> Any:
    if isinstance(conninfo, str) and conninfo.startswith("postgresql://"):
        kwargs.setdefault(
            "prepare_threshold",
            get_psycopg_connect_args()["prepare_threshold"],
        )
    return _ORIGINAL_PSYCOPG_CONNECT(conninfo, *args, **kwargs)


# Keep the pooled CI test surface on the same no-prepared-statements path as the app.
setattr(sa, "create_engine", _patched_create_engine)
setattr(psycopg, "connect", _patched_psycopg_connect)
