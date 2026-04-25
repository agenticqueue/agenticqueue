from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op


def add_column_if_not_exists(
    table_name: str,
    column: sa.Column[Any],
    *,
    schema: str | None = None,
    **kwargs: Any,
) -> None:
    if _has_column(table_name, column.name, schema=schema):
        return
    op.add_column(table_name, column, schema=schema, **kwargs)


def create_table_if_not_exists(
    table_name: str,
    *columns: sa.Column[Any],
    schema: str | None = None,
    **kwargs: Any,
) -> sa.Table | None:
    if _has_table(table_name, schema=schema):
        return None
    return op.create_table(table_name, *columns, schema=schema, **kwargs)


def create_index_if_not_exists(
    index_name: str,
    table_name: str,
    columns: Sequence[str],
    *,
    schema: str | None = None,
    **kwargs: Any,
) -> None:
    if _has_index(table_name, str(index_name), schema=schema):
        return
    op.create_index(index_name, table_name, columns, schema=schema, **kwargs)


def drop_column_if_exists(
    table_name: str,
    column_name: str,
    *,
    schema: str | None = None,
    **kwargs: Any,
) -> None:
    if not _has_column(table_name, column_name, schema=schema):
        return
    op.drop_column(table_name, column_name, schema=schema, **kwargs)


def drop_table_if_exists(
    table_name: str,
    *,
    schema: str | None = None,
    **kwargs: Any,
) -> None:
    if not _has_table(table_name, schema=schema):
        return
    op.drop_table(table_name, schema=schema, **kwargs)


def drop_index_if_exists(
    index_name: str,
    *,
    table_name: str,
    schema: str | None = None,
    **kwargs: Any,
) -> None:
    if table_name and not _has_index(table_name, str(index_name), schema=schema):
        return
    op.drop_index(index_name, table_name=table_name, schema=schema, **kwargs)


def create_unique_constraint_if_not_exists(
    constraint_name: str,
    table_name: str,
    columns: Sequence[str],
    *,
    schema: str | None = None,
    **kwargs: Any,
) -> None:
    if _has_constraint(table_name, str(constraint_name), schema=schema):
        return
    op.create_unique_constraint(
        constraint_name,
        table_name,
        columns,
        schema=schema,
        **kwargs,
    )


def drop_constraint_if_exists(
    constraint_name: str,
    table_name: str,
    *,
    schema: str | None = None,
    **kwargs: Any,
) -> None:
    if not _has_constraint(table_name, str(constraint_name), schema=schema):
        return
    op.drop_constraint(constraint_name, table_name, schema=schema, **kwargs)


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str, *, schema: str | None) -> bool:
    return _inspector().has_table(table_name, schema=schema)


def _has_column(
    table_name: str, column_name: str | None, *, schema: str | None
) -> bool:
    if not column_name or not _has_table(table_name, schema=schema):
        return False
    return any(
        column["name"] == column_name
        for column in _inspector().get_columns(table_name, schema=schema)
    )


def _has_index(table_name: str, index_name: str, *, schema: str | None) -> bool:
    if not _has_table(table_name, schema=schema):
        return False
    return any(
        index["name"] == index_name
        for index in _inspector().get_indexes(table_name, schema=schema)
    )


def _has_constraint(
    table_name: str, constraint_name: str, *, schema: str | None
) -> bool:
    if not _has_table(table_name, schema=schema):
        return False
    inspector = _inspector()
    constraints = inspector.get_unique_constraints(table_name, schema=schema)
    constraints.extend(inspector.get_foreign_keys(table_name, schema=schema))
    constraints.extend(inspector.get_check_constraints(table_name, schema=schema))
    pk = inspector.get_pk_constraint(table_name, schema=schema)
    if pk.get("name"):
        constraints.append(pk)
    return any(constraint.get("name") == constraint_name for constraint in constraints)
