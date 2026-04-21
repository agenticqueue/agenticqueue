from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for relative_path in ("apps/api/src", "apps/cli/src"):
    sys.path.insert(0, str(REPO_ROOT / relative_path))

DEFAULT_OUTPUT_PATH = REPO_ROOT / "openapi.json"


def build_openapi_spec() -> dict[str, object]:
    from agenticqueue_api.app import create_app

    app = create_app()
    return app.openapi()


def render_openapi_spec() -> str:
    return json.dumps(build_openapi_spec(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the canonical OpenAPI artifact for AgenticQueue."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Where to write the rendered OpenAPI document.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when the committed OpenAPI artifact differs from generated output.",
    )
    args = parser.parse_args()

    rendered = render_openapi_spec()
    output_path = args.output.resolve()

    if args.check:
        if not output_path.exists():
            print(
                f"OpenAPI drift: {output_path} is missing. "
                "Run `python scripts/generate_openapi.py`."
            )
            return 1

        existing = output_path.read_text(encoding="utf-8")
        if existing != rendered:
            print(
                f"OpenAPI drift detected in {output_path}. "
                "Run `python scripts/generate_openapi.py` and commit the result."
            )
            return 1

        print(f"OpenAPI artifact is current: {output_path}")
        return 0

    output_path.write_text(rendered, encoding="utf-8")
    print(f"Wrote OpenAPI artifact: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
