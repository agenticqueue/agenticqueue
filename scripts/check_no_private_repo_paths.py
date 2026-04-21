"""Fail fast if public-repo code reaches into the private workflow checkout."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DISALLOWED_PATTERNS = (
    "mmmmm-agenticqueue",
    "../mmmmm-agenticqueue",
    "..\\mmmmm-agenticqueue",
    "/home/runner/work/agenticqueue/mmmmm-agenticqueue",
    "D:/mmmmm/mmmmm-agenticqueue",
    "D:\\mmmmm\\mmmmm-agenticqueue",
)
TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".toml",
    ".yml",
    ".yaml",
    ".json",
    ".ini",
    ".cfg",
    ".txt",
}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=False,
    )
    entries = [item.decode("utf-8") for item in result.stdout.split(b"\x00") if item]
    return [REPO_ROOT / entry for entry in entries]


def should_scan(path: Path) -> bool:
    if path == Path(__file__).resolve():
        return False
    return path.suffix.lower() in TEXT_SUFFIXES


def main() -> int:
    violations: list[str] = []
    for path in tracked_files():
        if not should_scan(path):
            continue
        relative = path.relative_to(REPO_ROOT)
        content = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in DISALLOWED_PATTERNS:
            if pattern in content:
                violations.append(
                    f"{relative}: contains disallowed private path '{pattern}'"
                )
                break

    if violations:
        print(
            "Public repo files must not reference the private workflow checkout "
            "`mmmmm-agenticqueue`."
        )
        for violation in violations:
            print(violation)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
