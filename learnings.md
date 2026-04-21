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
