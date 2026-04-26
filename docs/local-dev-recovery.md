# Local Dev Recovery

Use this when a pre-1.0 local database came from an abandoned branch and API
startup fails during migrations. The AQ-302 startup guard prints a
`[MIGRATION_FAIL]` banner for this case; treat that banner as the signal to stop
the restart loop and inspect the local schema before retrying.

The examples below assume the default Compose project names:
`agenticqueue-db-1` for Postgres and `agenticqueue-api-1` for the API container.

## Stale .next cache after git pull

The dev Compose override bind-mounts `./apps/web` into the web container. After
a `git pull`, an old `apps/web/.next/` directory can still point at deleted
webpack chunks and make `/`, `/setup`, `/login`, or `/api/health` return 500.

The web dev startup hook runs only when `AQ_DEV_MODE=1` and
`NODE_ENV=development`; it removes `apps/web/.next/` before `next dev` starts.
Production images and CI test runs do not use that dev override path.

If a local container was started before this fix, restart it once:

```bash
docker compose restart web
```

## Why my dev DB has weird users

Playwright e2e runs must not write bootstrap users or auth audit rows into the
dev database. The e2e config enables test DB isolation with
`AGENTICQUEUE_USE_TEST_DATABASE=1` and points `DATABASE_URL_TEST` at
`agenticqueue_test`; setup recreates that database, runs Alembic migrations
against it, and teardown drops it. If `agenticqueue.users` or
`agenticqueue.auth_audit_log` contains test accounts such as `admin@localhost`
or `testclient` after an e2e run, treat that as dev DB pollution and check that
the suite used `apps/web/playwright.config.ts`, not a hand-started dev server.

## AUTO_SETUP orphan admin actor recovery

Older local demo paths could leave a stale `agenticqueue.actor` row with
`handle='admin'` after the corresponding `agenticqueue.users` row was manually
deleted. That state used to make `/setup` fail again because the actor handle is
unique.

Current bootstrap recovery preserves the stale actor for audit history by
renaming it to `admin-archived-<timestamp>` and setting `is_active=false`, then
creates a new active `admin` actor linked to the email submitted in `/setup`.
If you are cleaning a local demo database, delete only the unwanted user row and
rerun `/setup`; do not manually delete the actor row unless you are doing a full
schema reset.

## Detecting partial state

Start by comparing the database revision with the migration heads.

```bash
docker exec -it agenticqueue-api-1 sh -lc "uv run alembic -c apps/api/alembic.ini current"
docker exec -it agenticqueue-api-1 sh -lc "uv run alembic -c apps/api/alembic.ini heads"
docker exec -it agenticqueue-api-1 sh -lc "uv run alembic -c apps/api/alembic.ini history --verbose"
```

If startup printed `[MIGRATION_FAIL]`, copy these fields from the banner before
making changes:

- `current_rev`
- `target_rev`
- `failing_migration`
- `exception`

Then inspect the live schema directly:

```bash
docker exec -it agenticqueue-db-1 psql "postgresql://agenticqueue:agenticqueue@localhost:5432/agenticqueue"
```

Useful inspection queries:

```sql
SELECT version_num FROM alembic_version ORDER BY version_num;
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema = 'agenticqueue'
ORDER BY table_name;
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'agenticqueue' AND table_name = '<table_name>'
ORDER BY ordinal_position;
```

The common partial-state shape is: `alembic_version` says an older revision is
current, but one or more tables, columns, indexes, or constraints from a later
failed migration already exist.

## Manual schema repair

Read the failing migration before writing SQL. Only repair the specific object
that caused the failure; do not apply the whole migration by hand.

```bash
docker exec -it agenticqueue-api-1 sh -lc "sed -n '1,220p' apps/api/alembic/versions/<failing_migration>.py"
docker exec -it agenticqueue-db-1 psql "postgresql://agenticqueue:agenticqueue@localhost:5432/agenticqueue"
```

Use guarded SQL so the repair is safe to rerun while you are checking state:

```sql
CREATE SCHEMA IF NOT EXISTS agenticqueue;
ALTER TABLE agenticqueue.<table_name>
  ADD COLUMN IF NOT EXISTS <column_name> <type>;
CREATE INDEX IF NOT EXISTS <index_name>
  ON agenticqueue.<table_name> (<column_name>);
DROP INDEX IF EXISTS agenticqueue.<obsolete_index_name>;
```

For data backfills, prefer narrow updates with null guards:

```sql
UPDATE agenticqueue.<table_name>
SET <new_column> = <old_column>
WHERE <new_column> IS NULL AND <old_column> IS NOT NULL;
```

After each repair, rerun the detection commands. If a different object fails,
repeat this section for that object only.

## Alembic stamp + upgrade

If the database already contains the schema effects for a revision but
`alembic_version` was not advanced, stamp to the last revision that accurately
matches the repaired database, then upgrade normally.

```bash
docker exec -it agenticqueue-api-1 sh -lc "uv run alembic -c apps/api/alembic.ini stamp <known_good_revision>"
docker exec -it agenticqueue-api-1 sh -lc "uv run alembic -c apps/api/alembic.ini upgrade head"
docker exec -it agenticqueue-api-1 sh -lc "uv run alembic -c apps/api/alembic.ini current"
```

Use `stamp` only after inspection proves the schema really matches that
revision. Stamping past missing objects hides drift and makes the next migration
failure harder to diagnose.

Once `current` equals `heads`, restart the API:

```bash
docker compose restart api
docker compose logs --tail=120 api
```

The API should start without another `[MIGRATION_FAIL]` banner.

## Nuclear option: drop schema and re-bootstrap

Use this when local data is disposable or the schema is too far from `main` to
repair safely. This deletes local AgenticQueue data.

```bash
docker exec -it agenticqueue-db-1 psql "postgresql://agenticqueue:agenticqueue@localhost:5432/agenticqueue"
```

```sql
DROP SCHEMA IF EXISTS agenticqueue CASCADE;
DROP TABLE IF EXISTS alembic_version;
```

Then re-run migrations and let local setup seed the first admin user:

```bash
docker exec -it agenticqueue-api-1 sh -lc "uv run alembic -c apps/api/alembic.ini upgrade head"
docker compose restart api
docker compose logs --tail=120 api
```

If migration startup still fails after a clean schema drop, stop and treat it as
a real migration bug rather than local partial-state drift.
