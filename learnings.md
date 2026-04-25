# AgenticQueue Learnings

## 2026-04-24

### AQ-291: Alembic merge revisions can be order-sensitive under downgrade tests

```yaml
title: "Alembic merge parent order can affect downgrade traversal"
type: "pitfall"
what_happened: "AQ-291 first used merge revision `20260423_28` with `down_revision = (\"20260423_26\", \"20260423_27\")`. Fresh upgrade worked, but the migration reversibility test failed on downgrade because Alembic walked the auth branch down while the `20260423_27` actor-to-workspace foreign key still depended on the `workspace` table. A `depends_on` variant avoided that specific drop order but left multiple effective heads."
what_learned: "Alembic mergepoints can look correct on fresh upgrade while still producing an invalid downgrade traversal. In this repo, `depends_on` is not a substitute for consuming both branch heads when future migrations need a single linear head."
action_rule: "When converging AgenticQueue Alembic branches, run `alembic heads`, fresh `alembic upgrade head`, an existing-head-to-head upgrade simulation, and `tests/integration/test_migration.py`; if downgrade-to-base fails on dependent constraints, test the tuple merge parent order before changing schema semantics."
applies_when: "A migration ticket merges two or more AgenticQueue Alembic branches, especially when one branch adds outbound foreign keys to tables managed by the other branch."
does_not_apply_when: "The chain is already linear with one parent and `alembic heads` reports a single head without a mergepoint."
evidence:
  - "AQ-291 local verification showed `down_revision = (\"20260423_26\", \"20260423_27\")` failed the migration reversibility test during downgrade-to-base, while `down_revision = (\"20260423_27\", \"20260423_26\")` passed."
  - "`uv run alembic -c apps/api/alembic.ini heads` reported only `20260423_28 (head)` after the final merge revision."
  - "`uv run pytest --no-cov tests/integration/test_migration.py -q` passed on 2026-04-24 with 3 tests passing after the test was updated to downgrade explicitly to the merge parents."
  - "Fresh empty DB `uv run alembic -c apps/api/alembic.ini upgrade head` reached `20260423_28 (head) (mergepoint)`, and GitHub Actions for commit `aee7684` completed green across build, lint, pre-commit, scorecard, test, and CodeQL."
scope: "project"
confidence: "validated"
status: "active"
owner: "codex"
review_date: "2026-05-24"
```

### AQ-209: Auth pages outside a route group need their own scoped layout wrapper

```yaml
title: "Do not assume a URL belongs to the `(auth)` route group just because it is an auth page"
type: "frontend-routing"
what_happened: "AQ-209 required the concrete route file `apps/web/app/login/page.tsx`, but the shared auth fonts/tokens/grid from AQ-298 lived under `apps/web/app/(auth)/layout.tsx`, which route siblings do not inherit."
what_learned: "Next.js route groups are filesystem-only layout boundaries. A URL like `/login` can share auth visuals only if the file lives under that route group or gets an explicit route-local wrapper that imports the same scoped CSS/fonts."
action_rule: "When an auth ticket names a route outside `app/(auth)`, add a route-local layout that reuses the auth-scoped font variables and token/grid CSS, then test that the grid is present on the auth route and absent from shell routes like `/pipelines`."
applies_when: "Implementing setup, login, password reset, or other auth surfaces in the Next app."
does_not_apply_when: "The route file already lives under `app/(auth)` and inherits the auth layout directly."
evidence:
  - "`apps/web/app/login/layout.tsx` reuses the AQ-298 auth layout tokens/fonts for `/login` without touching the global app shell."
  - "`npx playwright test apps/web/e2e/login.spec.ts --project=chromium` passed with a grid-scope assertion on `/login` and `/pipelines`."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-24"
```

### AQ-301: Render SQLAlchemy URLs with passwords for migration fixtures

```yaml
title: "SQLAlchemy URL strings mask passwords unless explicitly rendered"
type: "tooling"
what_happened: "The AQ-301 migration idempotency fixture initially failed host and fallback Postgres auth because it passed `str(sa.URL)` to psycopg, which rendered the password as `***`."
what_learned: "SQLAlchemy URLs are safe for logs by default, but that behavior makes them unsafe as connection strings unless `render_as_string(hide_password=False)` is used."
action_rule: "When a test or migration helper passes a SQLAlchemy URL object to psycopg or an environment variable, render it with `hide_password=False` and keep masked forms only for logs."
applies_when: "Building temporary database fixtures or deriving sync/async database URLs from SQLAlchemy URL objects."
does_not_apply_when: "The URL is used only for human-readable logging or redacted diagnostics."
evidence:
  - "`uv run pytest apps/api/tests/test_migrations_idempotent.py -v` failed with repeated password authentication errors while using `str(sa.URL)`."
  - "`uv run pytest apps/api/tests/test_migrations_idempotent.py -v` passed (`2 passed`) after switching the fixture to `render_as_string(hide_password=False)`."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-25"
```

### AQ-300: Middleware e2e tests need a server-side API fixture, not browser route mocks

```yaml
title: "Next.js middleware tests need server-side upstream fixtures"
type: "tooling"
what_happened: "AQ-300's first auth-entry Playwright test failed correctly because `/` still rendered the old shell. After adding middleware, the e2e suite still needed a fake upstream API because Playwright `page.route()` mocks only browser-initiated requests, not middleware's server-side `fetch()` to `bootstrap_status`."
what_learned: "Auth entry-point coverage crosses the browser/server boundary, so tests must provide a real local upstream for middleware and must isolate bootstrap state between tests."
action_rule: "When testing Next.js middleware that calls AQ API routes or upstream services, run a Playwright webServer fixture for the upstream and reset its state after each test; do not rely on browser route mocks for middleware fetches."
applies_when: "A web e2e test verifies middleware redirects, server components, route handlers, or any behavior whose fetch happens outside the browser page context."
does_not_apply_when: "The fetch is performed from client-side React code and Playwright `page.route()` can intercept it deterministically."
evidence:
  - "`pnpm --filter web test:e2e --grep auth-entry-fresh` first failed with `Expected: 307 Received: 200` before middleware existed."
  - "`pnpm --filter web test:e2e` passed after adding `apps/web/e2e/support/auth-api-server.mjs`, serializing the web e2e worker, and resetting the fake bootstrap state."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-25"
```

### AQ-209: DoD screenshots need explicit files, not only Playwright attachments

```yaml
title: "Write Playwright screenshots to deterministic files when Plane closeout needs artifacts"
type: "verification"
what_happened: "The first AQ-209 Playwright helper used `testInfo.attach()` with only an in-memory screenshot body. The list reporter completed green but did not leave durable PNG files in `test-results/` for Plane closeout evidence."
what_learned: "A passing Playwright run may not preserve attachment bodies as browseable files unless the test also writes screenshots to an explicit path."
action_rule: "When a ticket DoD asks for screenshots or attachable artifacts, save them under a deterministic ignored directory such as `test-results/<ticket>/` and attach that path to the Playwright test info."
applies_when: "A frontend ticket requires screenshot, trace, video, or other visual artifacts as closeout evidence."
does_not_apply_when: "The DoD only requires a pass/fail smoke result and no artifact URLs or files."
evidence:
  - "`apps/web/e2e/login.spec.ts` now writes `test-results/aq209/aq209-login-empty.png`, `aq209-login-error.png`, and `aq209-login-mid-submit.png`."
  - "`npx playwright test apps/web/e2e/login.spec.ts --project=chromium` regenerated the three PNGs after the helper change."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-24"
```

### AQ-297: Ticket-local pytest paths can collide with existing basenames

```yaml
title: "DoD-specific test paths should not be added to global pytest discovery when basenames collide"
type: "tooling"
what_happened: "AQ-297 added required DoD tests at `apps/api/tests/test_auth.py` while the repo already had `tests/unit/test_auth.py`. Adding `apps/api/tests` to global `testpaths` produced an import-file mismatch under default pytest import mode; switching the whole repo to `--import-mode=importlib` avoided that mismatch but broke fixture discovery for existing suites."
what_learned: "Ticket-specific DoD test locations can be correct without being safe to add to the repo-wide pytest collection. Global pytest import-mode changes are a high-blast-radius workaround because they can alter fixture and package resolution across unrelated tests."
action_rule: "When a ticket mandates a test path outside the configured `testpaths`, run that path explicitly for DoD evidence; only add it to global discovery after checking for duplicate basenames and rerunning the full pytest collection without changing import mode."
applies_when: "A new AgenticQueue test file is created outside `tests/`, especially with a basename already present under the main test tree."
does_not_apply_when: "The new tests live under the existing `tests/` package with unique module names or the repo has already standardized on package-safe discovery for the extra path."
evidence:
  - "`uv run pytest --no-cov apps/api/tests/test_auth.py::test_session_rejects_username_field -q` first supplied the required AQ-297 red test evidence."
  - "A global `testpaths = [\"tests\", \"apps/api/tests\"]` plus `--import-mode=importlib` made the focused duplicate-basename case pass but caused broader fixture-resolution failures; reverting the global pytest change restored the suite."
  - "`uv run pytest --no-cov apps/api/tests/test_auth.py apps/api/tests/test_seed.py -q` and final `uv run pytest --no-cov -q` both passed on 2026-04-24 after keeping the DoD path explicit."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-24"
```

### AQ-299: Do not run Next build and standalone typecheck in parallel

```yaml
title: "Next build can race `tsc` by recreating `.next/types`"
type: "tooling"
what_happened: "During AQ-299 verification, `pnpm typecheck` was run in parallel with `npm --workspace @agenticqueue/web run build`. The typecheck failed with `TS6053` missing `.next/types/...` files while `next build` was recreating the generated types directory."
what_learned: "The web tsconfig includes `.next/types/**/*.ts`, so a standalone `tsc` process can race with Next's build output cleanup/generation if both commands run at the same time."
action_rule: "Run `next build` and `pnpm typecheck` sequentially in AgenticQueue web verification; if `TS6053` reports missing `.next/types` during parallel verification, rerun typecheck after the build has finished before treating it as a code failure."
applies_when: "Verifying Next.js app-router changes with both `next build` and standalone `tsc`."
does_not_apply_when: "The tsconfig no longer includes generated `.next/types` paths or the commands are isolated into separate worktrees."
evidence:
  - "`pnpm typecheck` failed once on 2026-04-24 with multiple `TS6053` missing `.next/types/...` files while `next build` was running in parallel."
  - "The same `pnpm typecheck` command passed immediately after `next build` completed."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-24"
```

### AQ-299: Redirect smoke tests should not depend on a future route's App Router behavior

```yaml
title: "Use hard browser redirects when guarding to a route that is implemented by a later ticket"
type: "frontend-testing"
what_happened: "The AQ-299 setup guard initially used `router.replace(\"/login\")`. The focused setup Playwright spec passed, but the full parallel Playwright suite sometimes rendered the Next 404 shell for the future `/login` route while `expect(page).toHaveURL(/\\/login$/)` still observed `/setup`."
what_learned: "Client-side App Router navigation to a route that does not exist yet can make redirect tests depend on not-found handling rather than the redirect contract. In a dependency chain where `/login` lands in the next ticket, the setup guard needs deterministic URL navigation."
action_rule: "When a guard redirects to a route that is intentionally implemented by a later ticket, prefer `window.location.replace(target)` or add a minimal target route before asserting the browser URL in Playwright."
applies_when: "A frontend ticket adds a guard to a not-yet-implemented route in the same planned chain."
does_not_apply_when: "The target route already exists and App Router navigation can resolve it normally."
evidence:
  - "`npx playwright test apps/web/e2e/setup.spec.ts --project=chromium` passed after AQ-299 implementation, but the full `npx playwright test --project=chromium` run failed once with the setup redirect test still observing `/setup` while the page snapshot was a 404."
  - "Switching the already-bootstrapped guard to `window.location.replace(\"/login\")` made both the focused setup smoke and the full Playwright suite pass."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-24"
```

### AQ-298: pnpm verification needs explicit workspace metadata in this npm-root repo

```yaml
title: "Add pnpm workspace metadata before making pnpm gates authoritative"
type: "tooling"
what_happened: "`pnpm typecheck` initially failed because `pnpm` was not on the Windows PATH; Corepack could run pnpm, but pnpm warned that the repo's npm-style `workspaces` field is not enough without `pnpm-workspace.yaml`."
what_learned: "When a DoD names a pnpm gate in this repo, Corepack may be the pnpm entrypoint and the repo still needs pnpm-native workspace metadata for clean output."
action_rule: "If a frontend ticket introduces or relies on a pnpm verification gate, confirm pnpm through Corepack, keep `pnpm-workspace.yaml` in sync with npm workspaces, and run the final gate as `pnpm <script>` once the shim is enabled."
applies_when: "A ticket requires `pnpm` verification in AgenticQueue's root workspace."
does_not_apply_when: "The repo has switched fully to another package manager and the ticket's DoD names that manager instead."
evidence:
  - "`pnpm typecheck` first failed on 2026-04-24 with `The term 'pnpm' is not recognized`."
  - "`corepack pnpm --version` returned `10.33.2`, and `pnpm typecheck` ran clean after enabling the shim and adding `pnpm-workspace.yaml`."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-24"
```

### AQ-297: SQL text call shapes must be stable under both ruff-format and Black

```yaml
title: "Avoid nested `sa.text` triple-quoted calls that ruff-format and Black reflow differently"
type: "tooling"
what_happened: "AQ-297 pushed new `apps/api/tests/test_auth.py` and `apps/api/tests/test_seed.py` files that passed local Black checks, but GitHub Actions `pre-commit` failed because `ruff-format` wrapped nested `session.scalar(sa.text(\"\"\"...\"\"\"))` and `session.execute(sa.text(\"\"\"...\"\"\")).one()` calls, then Black reformatted the same call sites again."
what_learned: "The repo runs both ruff-format and Black in pre-commit, so a test can be individually Black-clean while still unstable across the full hook order."
action_rule: "For multi-line SQL in tests, assign `sa.text(\"\"\"...\"\"\")` to a short local variable before passing it into `session.scalar()` or `session.execute()`; verify touched files with both `ruff format --check` and `black --check` using the pre-commit versions when CI reports formatter churn."
applies_when: "A Python test uses nested function calls around a triple-quoted SQL string or any other long multiline literal."
does_not_apply_when: "The multiline literal is already bound to a local variable or the repository has only one formatter enforcing the file."
evidence:
  - "GitHub Actions pre-commit run `24904435479` on `9ca7212` failed with `ruff-format` and Black both modifying `apps/api/tests/test_auth.py` and `apps/api/tests/test_seed.py`."
  - "`ruff format --check --diff apps/api/tests/test_auth.py apps/api/tests/test_seed.py` and `black --check --diff apps/api/tests/test_auth.py apps/api/tests/test_seed.py` both passed after the SQL text variables were introduced."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-24"
```

### AQ-293: Token format changes must update deterministic seed renderers too

```yaml
title: "Token renderers outside the live issuer can drift during prefix migrations"
type: "pitfall"
what_happened: "AQ-293 changed live API token rendering from the legacy `aq__<prefix>_<secret>` shape to `aq_live_<prefix><secret>` so first-run bootstrap could return `aq_live_...`, but the deterministic seed fixture still rendered `aq_live_<prefix>_<secret>`. The full pytest suite then failed `tests/integration/test_seed_idempotency.py::test_seed_happy_path_creates_expected_entities_and_claimable_task` with a 401 because seeded tokens no longer parsed."
what_learned: "AgenticQueue has more than one token-rendering path: runtime token issuance and deterministic seed fixture rendering. A prefix or delimiter change must update both paths, or seed smoke tests can generate tokens that the auth parser rejects."
action_rule: "When changing token prefix, delimiter, display-prefix, or parser semantics, grep for `render_raw_token`, `token_display_prefix`, `_render_token`, and seed fixtures; then run both auth tests and `tests/integration/test_seed_idempotency.py` before broad verification."
applies_when: "An auth ticket changes API token shape, token parsing, token display prefixes, bootstrap first-token issuance, or deterministic local seed output."
does_not_apply_when: "The change is limited to session cookies or password handling and does not touch API token rendering or parsing."
evidence:
  - "`uv run pytest --no-cov -q` first failed on 2026-04-24 with `tests/integration/test_seed_idempotency.py::test_seed_happy_path_creates_expected_entities_and_claimable_task` returning 401 from `/v1/tasks`."
  - "`apps/api/src/agenticqueue_api/seed.py::SeedToken.render_raw_token()` still inserted an underscore separator after `token_display_prefix()` while `authenticate_api_token()` parsed the new `aq_live_` token body as fixed 16-character hash prefix plus raw secret."
  - "After aligning `SeedToken.render_raw_token()` with the new issuer shape, the seed test passed and the full suite passed with `719 passed`."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-24"
```

### AQ-271: Tuple-style None guards do not narrow constructor arguments for mypy

```yaml
title: "Tuple-style None guards need explicit asserts before typed constructor calls"
type: "tooling"
what_happened: "AQ-271 introduced `resolve_contract_dod_items()` in `apps/api/src/agenticqueue_api/dod.py` and locally passed the focused pytest + pre-commit slice, but GitHub Actions `lint` on `b75076c1ed15106a231100c94225204e7691ea02` failed in mypy because the `if None in (...)` guard did not narrow the local `str | None` variables before they were passed into the `ContractDodItem` dataclass constructor."
what_learned: "In this repo's typed Python surface, tuple-style `None` guards are not strong enough for mypy to prove constructor arguments are non-optional; explicit `assert value is not None` (or equivalent per-variable narrowing) is required, and the touched-file pre-commit slice does not catch that because mypy runs separately in CI."
action_rule: "When optional locals feed a typed dataclass or model constructor, run `uv run --with mypy mypy .` (or at least the touched module) before pushing and add explicit per-variable narrowing after any tuple-style or aggregate `None` guard."
applies_when: "A typed AgenticQueue module filters `str | None` or similar optionals and then passes those values into a dataclass, Pydantic model, or other constructor that expects concrete types."
does_not_apply_when: "The values are already non-optional at declaration time or each variable is narrowed through a direct `if value is None: continue` branch that mypy can follow."
evidence:
  - "GitHub Actions run `24881227546` on `b75076c1ed15106a231100c94225204e7691ea02` failed with five mypy `arg-type` errors in `apps/api/src/agenticqueue_api/dod.py`."
  - "`uv run --with mypy mypy .` passed on 2026-04-24 after commit `6ef21a83c90b60a903e36e3d4a37a694c474aa5e` added explicit asserts before constructing `ContractDodItem`."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-24"
```

### AQ-266: Direct-to-main formatter jobs can surface untouched repo drift

```yaml
title: "Whole-repo formatter jobs can fail on untouched files after a small direct push"
type: "repo-behavior"
what_happened: "AQ-266 reconciled the unified MCP surface and pushed a small set of MCP/doc changes to `main`, but GitHub Actions `pre-commit` on `d23b10c` and then `72f89d5` failed on formatter rewrites outside the intended feature diff. After the touched conformance import was fixed, the CI logs still surfaced Ruff-format-only changes in `tests/mcp/test_task_type_authz.py` and `tests/mcp/test_task_type_parity.py`, which were pre-existing style drift."
what_learned: "In this repo, direct-to-main slots inherit the full repository formatter state because `pre-commit` and `lint` run over all tracked Python files, not just the files changed by the ticket."
action_rule: "When a direct push turns `pre-commit` or formatter-driven `lint` red, inspect the failed job's rewritten diff before assuming the feature logic is wrong; if the diff is pure no-op formatting debt in other files, fix-forward the minimal formatting cleanup and continue."
applies_when: "A GitHub Actions `pre-commit` or `lint` failure shows formatter-generated diffs in files outside the current ticket's intended scope."
does_not_apply_when: "The failed diff points at the files you just changed semantically or the failing job is a real logic/type/test error rather than formatter output."
evidence:
  - "GitHub Actions run `24879982402` on `d23b10ced4fa01db114be2a6a13f89b69f2b2fe4` first failed because `tests/mcp/test_conformance.py` needed formatter wrapping."
  - "GitHub Actions run `24880277550` on `72f89d5f4b23fad8cd26fa707ee75293474b90ea` then failed with Ruff-format diffs in untouched files `tests/mcp/test_task_type_authz.py` and `tests/mcp/test_task_type_parity.py`."
  - "`uv run pytest tests/mcp/test_task_type_authz.py tests/mcp/test_task_type_parity.py -q` passed on 2026-04-24 after the no-op formatting cleanup."
scope: "project"
confidence: "validated"
status: "active"
owner: "codex"
review_date: "2026-05-24"
```

### AQ-266: FastMCP middleware overrides must keep the base Sequence signatures

```yaml
title: "FastMCP middleware overrides must mirror the base `Sequence[Tool]` typing"
type: "tooling"
what_happened: "AQ-266 added `AgenticQueueToolVisibilityMiddleware` for profile-filtered `tools/list` output and initially typed `on_list_tools()` with `CallNext[..., list[Tool]]` returning `list[Tool]`. The runtime behavior worked, but GitHub Actions `lint` on `8d95666` failed in mypy because FastMCP's `Middleware.on_list_tools()` contract uses `Sequence[Tool]`."
what_learned: "FastMCP middleware hook annotations are part of the enforced typing contract; narrowing a callback or return container type breaks mypy even when the code returns a compatible concrete list at runtime."
action_rule: "When overriding FastMCP middleware hooks, copy the base-class `CallNext[...]` and return annotations exactly; for `on_list_tools()`, use `Sequence[Tool]` rather than a narrower `list[Tool]` signature."
applies_when: "Adding or editing subclasses of `fastmcp.server.middleware.middleware.Middleware` in the AgenticQueue MCP layer."
does_not_apply_when: "The override does not change the hook signature or the upstream base class already uses the narrower type."
evidence:
  - "GitHub Actions run `24880326396` on `8d95666b682fa4feddb4918a7e0a806dfd21758b` failed with `apps/api/src/agenticqueue_api/mcp/visibility.py:46: error: Argument 2 of \\\"on_list_tools\\\" is incompatible with supertype \\\"Middleware\\\"`."
  - "`uv run pytest tests/aq/test_mcp_server.py tests/mcp/test_conformance.py::test_tool_listing_matches_canonical_surface[http] tests/mcp/test_task_type_authz.py tests/mcp/test_task_type_parity.py -q` passed on 2026-04-24 after the signature was aligned."
scope: "project"
confidence: "validated"
status: "active"
owner: "codex"
review_date: "2026-05-24"
```

### AQ-267: Mutating MCP adapters must carry idempotency keys when routed through REST

```yaml
title: "MCP mutation adapters must synthesize idempotency keys when delegating to REST"
type: "repo-behavior"
what_happened: "AQ-267 normalized `register_task_type` and `update_task_type` in `apps/api/src/agenticqueue_api/mcp/submit_tools.py` to call the internal REST task-type routes instead of mutating the registry directly. The first parity run failed because the REST mutation path enforces `Idempotency-Key`, but the MCP adapter forwarded the payload without that header."
what_learned: "Once an MCP tool becomes a thin wrapper over a mutating REST route, parity requires inheriting the REST surface's idempotency contract instead of bypassing it."
action_rule: "When an AgenticQueue MCP tool delegates a POST/PATCH/DELETE operation to an internal REST endpoint, include a deterministic `Idempotency-Key` derived from stable request content unless the route explicitly documents that no idempotency header is required."
applies_when: "A mutating MCP tool uses `call_internal_api()` or another adapter to reach a REST endpoint guarded by the API's idempotency middleware."
does_not_apply_when: "The MCP tool is read-only or the delegated route explicitly bypasses idempotency enforcement."
evidence:
  - "`uv run pytest tests/mcp/test_task_type_authz.py tests/mcp/test_task_type_parity.py -q` failed on 2026-04-24 with `Idempotency-Key header is required` after `update_task_type` was first routed through the REST surface without a synthesized header."
  - "`uv run pytest tests/mcp/test_task_type_authz.py tests/mcp/test_task_type_parity.py tests/mcp/test_conformance.py tests/aq/test_mcp_server.py tests/api/test_surface_parity.py tests/unit/test_task_type_registry.py -q` passed on 2026-04-24 after the MCP mutation adapters added deterministic UUIDv5 idempotency headers."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-24"
```

### AQ-264: Lazy router extraction needs module-global model rebinding

```yaml
title: "Lazily extracted FastAPI routers must rebind imported model classes into module globals"
type: "tooling"
what_happened: "AQ-264 moved the auth/token endpoints into `routers/auth_tokens.py` and initially referenced request/response models as `app_module.*` inside the builder. FastAPI accepted the route definitions, but request parsing and OpenAPI generation failed because Pydantic could not fully resolve those builder-local forward references."
what_learned: "For builder-style router extraction in this repo, lazy imports from `agenticqueue_api.app` are safe only if the route model names are rebound into the router module's global namespace before defining the endpoints."
action_rule: "When extracting FastAPI routes into a builder that lazily imports schema classes from another module, inject the imported classes into `globals()` before declaring the route functions so FastAPI and Pydantic can resolve annotations at runtime and during OpenAPI generation."
applies_when: "A dedicated router module imports request or response models lazily from `agenticqueue_api.app` or another module at builder-call time."
does_not_apply_when: "The router owns its schema classes directly or imports them normally at module import time without circular-import risk."
evidence:
  - "`uv run pytest tests/unit/test_auth.py tests/unit/test_auth_token_router_structure.py tests/api/test_surface_parity.py -q` failed on 2026-04-24 with a 422 on `/v1/auth/tokens` and a `PydanticUserError` for `app_module.RotateOwnKeyRequest | None` before the global rebinding fix."
  - "The same pytest command and `uv run --with mypy mypy apps/api/src/agenticqueue_api/app.py apps/api/src/agenticqueue_api/routers/auth_tokens.py tests/unit/test_auth.py tests/unit/test_auth_token_router_structure.py` both passed on 2026-04-24 after rebinding the lazy-imported classes into `routers/auth_tokens.py` globals."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-24"
```

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

### AQ-278: Vitest needs the React Vite plugin to load Next TSX under this repo

```yaml
title: "Root Vitest runs need `@vitejs/plugin-react` before they can import Next TSX modules"
type: "tooling"
what_happened: "AQ-278 added a render-level regression for the login screen, but `npx vitest run apps/web/components/agenticqueue-web-app.test.tsx` initially failed before executing any assertions because the repo-level Vitest setup tried to import `apps/web/components/agenticqueue-web-app.tsx` under Next's `jsx: preserve` config without the React Vite plugin."
what_learned: "In this repo, a bare `vitest.config.ts` is not enough for TSX files that live under the Next app; Vitest needs `@vitejs/plugin-react` plus the web alias mapping so tests can import the real component graph instead of failing during Vite import analysis."
action_rule: "When adding the first Vitest coverage for a Next TSX surface in AgenticQueue, wire `@vitejs/plugin-react` into `vitest.config.ts` and map `@` to `apps/web` before blaming the test or component for import-analysis failures."
applies_when: "A new Vitest test imports files from `apps/web/**` and Vite reports parse or import-analysis errors tied to `jsx: preserve` or unresolved `@/...` imports."
does_not_apply_when: "The test only touches plain TS modules outside the Next web app or the repo already has a working React/Vite test harness."
evidence:
  - "`npx vitest run apps/web/components/agenticqueue-web-app.test.tsx` failed on 2026-04-24 with `Failed to parse source for import analysis` against `apps/web/components/agenticqueue-web-app.tsx` until `@vitejs/plugin-react` and the `@ -> apps/web` alias were added to `vitest.config.ts`."
  - "The same command passed on 2026-04-24 after the Vitest config was updated and the login screen test executed normally."
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

### AQ-265: Verify API router tickets against a fresh migrated temp database when the shared local DB drifts

```yaml
title: "Fresh migrated temp databases are safer than the long-lived local dev DB for API verification"
type: "tooling"
what_happened: "The first AQ-265 verification run failed on `tests/api/test_roles.py` with an empty `/v1/roles` result because the shared local `agenticqueue` database had drifted to an invalid Alembic head (`20260423_26`) and no seeded `role` rows."
what_learned: "A long-lived local verification database can silently diverge from `main`, so a focused API/router ticket can look broken when the actual issue is stale schema state rather than the code diff."
action_rule: "If AgenticQueue API verification fails on missing seeded data or an invalid Alembic revision in the shared local DB, create a fresh temporary Postgres database, run `uv run alembic -c apps/api/alembic.ini upgrade head`, point `AGENTICQUEUE_DATABASE_URL` at that database, and rerun the required pytest command there."
applies_when: "You are verifying FastAPI/API-route changes locally and the default `agenticqueue` database shows migration drift, missing seed rows, or other stale-schema symptoms."
does_not_apply_when: "The shared local database is already at the current Alembic head and the failure reproduces on a freshly migrated database."
evidence:
  - "`uv run pytest tests/api/test_roles.py tests/unit/test_capability_crud.py tests/unit/test_capability_enforcement.py tests/api/test_surface_parity.py -q` failed on 2026-04-24 against the shared local DB with an empty `/v1/roles` response."
  - "`uv run alembic -c apps/api/alembic.ini upgrade head` succeeded against temp database `aq_rbac_verify_1777024515`, and the same pytest command then passed (`71 passed`) with `AGENTICQUEUE_DATABASE_URL=postgresql+asyncpg://agenticqueue:agenticqueue@127.0.0.1:54329/aq_rbac_verify_1777024515?prepared_statement_cache_size=0`."
scope: "project"
confidence: "confirmed"
status: "active"
owner: "codex"
review_date: "2026-05-24"
```
