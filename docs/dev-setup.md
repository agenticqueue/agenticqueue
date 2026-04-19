# Developer Setup

## Pre-commit hooks

Install `pre-commit` once with `pipx`, then register the hooks in your local
clone:

```bash
pipx install pre-commit
pre-commit install
```

Before opening a PR, run the full hook set from the repo root:

```bash
pre-commit run --all-files
```

The configured hooks cover Ruff linting and formatting, Black formatting, LF
line endings, end-of-file normalization, trailing-whitespace cleanup, and basic
YAML/JSON validation.
