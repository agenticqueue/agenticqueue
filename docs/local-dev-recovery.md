# Local Dev Recovery

Use this when a pre-1.0 local database came from an abandoned branch and API
startup fails during migrations. The AQ-302 startup guard prints a
`[MIGRATION_FAIL]` banner for this case; treat that banner as the signal to stop
the restart loop and inspect the local schema before retrying.

The examples below assume the default Compose project names:
`agenticqueue-db-1` for Postgres and `agenticqueue-api-1` for the API container.

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
