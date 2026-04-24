# AgenticQueue Learnings

## 2026-04-24

### AQ-263: Alias regression tests for stateful POST routes need reset state

```yaml
title: "Alias-vs-canonical POST route checks need reset fixture state and metadata normalization"
type: "tooling"
what_happened: "AQ-263 added a hidden-alias regression for the learning draft edit/reject/confirm endpoints. Comparing canonical and alias POST responses in one shared database state produced false diffs because confirm created a learning on the first call and reject/edit responses carried per-run `run://...` evidence and record IDs."
what_learned: "For stateful POST routes, alias-vs-canonical regression tests only stay meaningful when each request starts from the same reset fixture state and the assertion ignores run-specific IDs and evidence URIs."
action_rule: "When verifying hidden legacy aliases for AgenticQueue POST endpoints, reseed or reset the test database between canonical and alias calls and compare only stable semantic fields after normalizing per-run metadata."
applies_when: "An integration test compares canonical and hidden alias responses for endpoints that mutate drafts, learnings, or other persisted records."
does_not_apply_when: "The endpoint is read-only or the response shape is already deterministic and free of per-run IDs, timestamps, or evidence URIs."
evidence:
  - "`uv run pytest tests/integration/test_learning_draft_api.py -q` failed on 2026-04-24 until the alias assertions reset the shared Postgres state between canonical and alias POSTs and normalized `run://...` evidence values."
  - "`uv run pytest tests/unit/test_learnings_router.py tests/integration/test_learning_draft_api.py -q` passed on 2026-04-24 after the reset-state alias regression was in place."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-24"
```

## 2026-04-23

### AQ-294: Authenticated requests must not open a second ORM session

```yaml
title: "Authenticated request middleware must share the route's DB session"
type: "repo-behavior"
what_happened: "The REST hardening soak on main timed out `GET /v1/workspaces?limit=1` under the CI profile because bearer-authenticated requests opened one ORM session in auth middleware and a second session in the route dependency."
what_learned: "Under load, that double-session pattern inflates pool usage above actor count and can turn a healthy read path into request-budget timeouts even when query latency looks normal."
action_rule: "For authenticated API requests, create one request-scoped ORM session in middleware and reuse it from downstream dependencies instead of opening a second session per route."
applies_when: "A middleware layer authenticates or annotates requests using database access before FastAPI dependencies run."
does_not_apply_when: "The middleware is fully stateless or the downstream path never opens another ORM session."
evidence:
  - "`SOAK_CI_MODE=true uv run python scripts/audit_rest_hardening.py --output-json dist/rest-hardening-matrix.json --soak-output-json dist/rest-hardening-soak.json --soak-seconds 300 --actors 100 --rps-per-actor 10 --max-read-p99-ms 200` passed on 2026-04-23 after the session-sharing fix with `request_count=1180`, `sample_exceptions=[]`, `timed_out_actors=0`, `peak_checked_out=10`, and `p99_ms=207.77`."
  - "GitHub Actions run `24870712151` on `93862a80b43ba9c53572f5e886da37f0a18ded4c` failed before the fix with 10 request-budget timeout exceptions on `/v1/workspaces?limit=1`."
scope: "project"
confidence: "validated"
status: "active"
owner: "codex"
review_date: "2026-05-23"
```

### AQ-294: Shared auth sessions must rollback handled flush failures

```yaml
title: "Shared auth sessions must rollback before middleware closeout after handled write errors"
type: "pitfall"
what_happened: "The first AQ-294 fix-forward made the `test` workflow red because duplicate-create API paths now reused the auth session; when a route handled a `UniqueViolation` and returned a structured error, middleware still tried to `commit()` the session and raised `PendingRollbackError`."
what_learned: "When middleware owns the request-scoped session, a handled flush failure can leave that session in partial-rollback state even though the HTTP response is already the correct structured 4xx."
action_rule: "After `call_next`, middleware that owns the shared ORM session must check whether the session is still active; commit only on a healthy transaction and rollback otherwise."
applies_when: "A request-scoped session is created in middleware and reused by handlers that may catch database write errors and convert them into API responses."
does_not_apply_when: "The route dependency owns commit/rollback itself or the request path is read-only and never reaches a handled flush error."
evidence:
  - "`uv run pytest tests/entities/test_router_contract.py::test_duplicate_create_invalid_filter_invalid_value_invalid_payload_and_immutable_patch_are_structured -q` failed locally on `ac0812992547ff35236b892eb397bc006d252544` and passed after the rollback-on-inactive-session fix on 2026-04-23."
  - "GitHub Actions run `24872146073` on `31caaf247d3f4bba7136a75f63f466de959f31ae` passed the full `test` workflow after the follow-up fix."
scope: "project"
confidence: "validated"
status: "active"
owner: "codex"
review_date: "2026-05-23"
```

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
