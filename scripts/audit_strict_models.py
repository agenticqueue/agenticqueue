"""Audit strict submit-schema models for required bounds and config."""

from __future__ import annotations

import ast
from pathlib import Path

SUBMIT_FILE = (
    Path(__file__).resolve().parents[1]
    / "apps"
    / "api"
    / "src"
    / "agenticqueue_api"
    / "schemas"
    / "submit.py"
)
ALLOWED_SIMPLE_TYPES = {"bool", "date", "datetime"}


def _annotation_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _node_uses_bounds(node: ast.AST, bounded_aliases: set[str]) -> bool:
    text = ast.unparse(node)
    if "Field(" in text or "StringConstraints(" in text:
        return True
    name = _annotation_name(node)
    if name is not None and name in bounded_aliases:
        return True
    if isinstance(node, ast.Subscript):
        value_name = _annotation_name(node.value)
        if value_name in {"list", "dict", "Annotated"} and "Field(" in text:
            return True
        return _node_uses_bounds(node.slice, bounded_aliases)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _node_uses_bounds(node.left, bounded_aliases) and _node_uses_bounds(
            node.right, bounded_aliases
        )
    return False


def _field_needs_bounds(node: ast.AST, strict_models: set[str]) -> bool:
    name = _annotation_name(node)
    if name in strict_models or name in ALLOWED_SIMPLE_TYPES:
        return False
    if isinstance(node, ast.Attribute) and node.attr in ALLOWED_SIMPLE_TYPES:
        return False
    text = ast.unparse(node)
    return any(token in text for token in ("str", "int", "list", "dict", "Literal"))


def main() -> int:
    source = SUBMIT_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SUBMIT_FILE))
    violations: list[str] = []

    if 'model_config = ConfigDict(strict=True, extra="forbid")' not in source:
        violations.append("StrictSchemaModel must set strict=True and extra='forbid'.")

    bounded_aliases: set[str] = set()
    strict_models: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target_name = _annotation_name(node.targets[0])
            if target_name and _node_uses_bounds(node.value, set()):
                bounded_aliases.add(target_name)
        if isinstance(node, ast.ClassDef):
            if any(
                _annotation_name(base) == "StrictSchemaModel" for base in node.bases
            ):
                strict_models.add(node.name)

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(
            _annotation_name(base) == "StrictSchemaModel" for base in node.bases
        ):
            continue
        for item in node.body:
            if not isinstance(item, ast.AnnAssign) or not isinstance(
                item.target, ast.Name
            ):
                continue
            field_name = item.target.id
            if field_name == "model_config":
                continue
            if not _field_needs_bounds(item.annotation, strict_models):
                continue
            if _node_uses_bounds(item.annotation, bounded_aliases):
                continue
            if item.value is not None and "Field(" in ast.unparse(item.value):
                continue
            violations.append(f"{node.name}.{field_name} is missing an explicit bound.")

    if violations:
        for violation in violations:
            print(f"[strict-model-audit] {violation}")
        return 1

    print("[strict-model-audit] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
