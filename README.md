# AgenticQueue

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/agenticqueue/agenticqueue/badge)](https://scorecard.dev/viewer/?uri=github.com/agenticqueue/agenticqueue)

AgenticQueue is an Apache-2.0 coordination plane for humans and agents.

## Install

```bash
git clone https://github.com/agenticqueue/agenticqueue.git
cd agenticqueue
cp .env.example .env
uv sync
docker compose up -d db pgbouncer
uv run alembic -c apps/api/alembic.ini upgrade head
```

Phase 1 starts with a minimal Postgres + PgBouncer foundation in `apps/api/`.
The pooler runs in transaction mode, so psycopg paths disable prepared
statements via `prepare_threshold=None` and asyncpg paths use
`prepared_statement_cache_size=0`. The full API, worker, and UI land in later
tickets, but `docker-compose.yml` now defines an `api` service that is gated on
PgBouncer health for future containerized smoke tests.

## Repo Map

- `README.md` - project overview and quick start
- `CONTRIBUTING.md` - contribution flow, DCO requirements, review expectations
- `SECURITY.md` - responsible disclosure process
- `CODE_OF_CONDUCT.md` - community standards
- `CHANGELOG.md` - release notes in Keep a Changelog format
- `apps/api/` - Alembic config, migration scaffolding, and future FastAPI code
- `apps/web/` - Next.js 15 Phase 7 shell with bearer-token login and route-aware nav
- `tests/integration/` - integration coverage for migrations and future API work

Planned code surfaces from the accepted build plan:

- `apps/web/` - Next.js UI
- `packages/` - shared contracts, SDKs, and utilities
- `infra/` - local dev and deployment support
- `docs/` - public documentation and architecture notes

## Status

This repository is in early Phase 1. Baseline OSS files, CI, and migration
scaffolding are in place before the first entity models and API endpoints land.

## License

AgenticQueue is licensed under Apache-2.0. The `AgenticQueue` name and related marks are reserved separately.
