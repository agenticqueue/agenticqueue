# AgenticQueue Learnings

## 2026-04-21

### AQ-137: Shared test DB modules must run sequentially

```yaml
title: "Shared Postgres pytest modules are not safe to parallelize"
type: "pitfall"
what_happened: "Running `tests/integration/test_seed_idempotency.py` and `tests/api/test_openapi.py` in parallel produced a duplicate-actor failure that disappeared when the same modules were rerun one at a time."
what_learned: "These integration and API suites share the same Postgres test database lifecycle, so cross-module parallelism can fabricate failures that are not real regressions."
action_rule: "Run AgenticQueue pytest modules that seed, truncate, or mutate the shared Postgres test database sequentially unless the fixtures are explicitly isolated per worker."
applies_when: "Verification touches `tests/integration/*` or `tests/api/*` modules that rely on the shared local Postgres test database."
does_not_apply_when: "The suite is documented as xdist-safe or each worker gets its own isolated database/schema."
evidence:
  - "`pytest D:/mmmmm/agenticqueue/tests/integration/test_seed_idempotency.py -q` passed when rerun sequentially on 2026-04-21."
  - "The parallel run failure was a duplicate-actor count mismatch that did not reproduce outside the concurrent run."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-21"
```

### AQ-137: OpenAPI artifact must be regenerated on schema drift

```yaml
title: "Regenerate `openapi.json` when FastAPI schema output changes"
type: "repo-behavior"
what_happened: "After hiding `/setup` from the schema, `tests/api/test_openapi.py` still failed because the served `ValidationError` component no longer matched the checked-in `openapi.json` artifact."
what_learned: "This repo treats `openapi.json` as a canonical artifact, so schema-shape drift can fail CI even when route paths match and the code change is otherwise correct."
action_rule: "When an API change or framework drift alters the served schema, run `python scripts/generate_openapi.py` and commit the updated `openapi.json` before closing the ticket."
applies_when: "An API-facing ticket changes route metadata, response models, or any dependency that affects the generated FastAPI schema."
does_not_apply_when: "The ticket does not affect the served API schema and `python scripts/generate_openapi.py --check` reports no drift."
evidence:
  - "`python D:/mmmmm/agenticqueue/scripts/generate_openapi.py --check` reported drift on 2026-04-21."
  - "`pytest D:/mmmmm/agenticqueue/tests/api/test_openapi.py -q` passed after regenerating `openapi.json`."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-21"
```

### AQ-137: Runtime seed fixtures must be repo-root aware and image-packaged

```yaml
title: "Startup seed fixtures must resolve from the repo root and ship in the API image"
type: "repo-behavior"
what_happened: "The first isolated compose boot failed during auto-setup because `load_seed_fixture()` looked for `examples/seed.yaml` relative to the working directory and the API image did not copy the `examples/` tree."
what_learned: "A fixture used by runtime startup is part of the application artifact, not just the source checkout, so both path resolution and Docker packaging have to treat it as production input."
action_rule: "Resolve runtime seed fixtures from `get_repo_root()` and copy the required `examples/` files into API images whenever startup or CLI paths depend on them."
applies_when: "A runtime bootstrap or CLI path reads checked-in fixture files from `examples/`."
does_not_apply_when: "The fixture is test-only or the runtime payload is injected externally instead of being read from the repo."
evidence:
  - "The isolated `docker compose up -d` verification failed until `examples/seed.yaml` was resolved from the repo root and copied into `apps/api/Dockerfile` on 2026-04-21."
  - "The fresh `aq137verify4` compose stack emitted the one-time token and served `/v1/workspaces` after the image packaging fix."
scope: "project"
confidence: "validated"
status: "active"
owner: "codex"
review_date: "2026-05-21"
```

### AQ-137: Direct DB rewrites must support alternate PgBouncer host ports

```yaml
title: "Direct database rewrites cannot hardcode only 6432 and 64329"
type: "tooling"
what_happened: "A host-side startup repro against an isolated stack on port `64331` still hit PgBouncer during Alembic because the direct-DB rewrite only special-cased `6432` and `64329`."
what_learned: "Local verification stacks often shift PgBouncer to another `643xx` port, so hardcoded direct-port rewrites break exactly the fallback path used to debug startup issues."
action_rule: "Derive direct Postgres ports generically for `643xx -> 543xx` mappings, while still honoring an explicit `AGENTICQUEUE_DB_PORT` override when provided."
applies_when: "A helper needs to bypass PgBouncer for migrations or direct Postgres access outside the default local port pair."
does_not_apply_when: "The caller already provides an explicit direct database URL that does not need port rewriting."
evidence:
  - "A `TestClient(create_app())` startup repro against `127.0.0.1:64331` failed with `asyncpg.exceptions.InvalidSQLStatementNameError` before the generic rewrite landed on 2026-04-21."
  - "The same startup repro succeeded and printed the one-time token after the generic `643xx -> 543xx` mapping was added."
scope: "project"
confidence: "validated"
status: "active"
owner: "codex"
review_date: "2026-05-21"
```
