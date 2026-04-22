from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from urllib.parse import parse_qsl
from urllib.parse import urlsplit

_ROW_RE = re.compile(r"^(?P<sequence>\d+\.\d+)\s+(?P<marker>[○✦])$")
_REST_RE = re.compile(r"^(GET|POST|PATCH|DELETE)\s+(\S+)$")
_PATH_PARAM_RE = re.compile(r"\{[^}]+\}")


@dataclass(frozen=True)
class SurfaceOperation:
    sequence: str
    marker: str
    name: str
    cli: str
    method: str
    path: str
    query_params: tuple[str, ...]
    caps: str
    ticket: str

    @property
    def is_mutation(self) -> bool:
        return self.marker == "✦"

    @property
    def is_list_like(self) -> bool:
        lowered = self.name.lower()
        return lowered.startswith("list ") or lowered == "query audit"

    @property
    def normalized_path(self) -> str:
        return normalize_path_template(self.path)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def surface_doc_path() -> Path:
    return repo_root() / "docs" / "surface-1.0.md"


def normalize_path_template(path: str) -> str:
    return _PATH_PARAM_RE.sub("{param}", path)


def _split_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


@lru_cache(maxsize=1)
def load_surface_operations(
    surface_path: Path | None = None,
) -> tuple[SurfaceOperation, ...]:
    path = surface_doc_path() if surface_path is None else surface_path
    operations: list[SurfaceOperation] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue

        cells = _split_cells(line)
        if len(cells) < 7:
            continue

        row_match = _ROW_RE.fullmatch(cells[0])
        if row_match is None:
            continue

        rest_cell = cells[3].strip("`")
        rest_match = _REST_RE.fullmatch(rest_cell)
        if rest_match is None:
            continue

        method, raw_url = rest_match.groups()
        parsed = urlsplit(raw_url)
        query_params = tuple(
            name for name, _ in parse_qsl(parsed.query, keep_blank_values=True)
        )
        operations.append(
            SurfaceOperation(
                sequence=row_match.group("sequence"),
                marker=row_match.group("marker"),
                name=cells[1],
                cli=cells[2].strip("`"),
                method=method,
                path=parsed.path,
                query_params=query_params,
                caps=cells[5].strip("`"),
                ticket=cells[6],
            )
        )

    return tuple(operations)
