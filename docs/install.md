# Install

AgenticQueue currently ships one canonical local stack: Postgres + PgBouncer +
API + web UI via `docker compose`. The UI is always part of the stack. Human
review is the thing that toggles, not observability.

You do not need an LLM API key to boot AgenticQueue.

## Prerequisites

- Docker Desktop or Docker Engine with Compose v2
- `uv` for the local CLI commands

## Quick start

```bash
git clone https://github.com/agenticqueue/agenticqueue.git
cd agenticqueue
cp .env.example .env
uv sync --frozen
docker compose up -d
```

The compose stack defaults to:

- API: `http://127.0.0.1:8000`
- Web UI: `http://127.0.0.1:3000`
- Postgres: `127.0.0.1:5432`
- PgBouncer: `127.0.0.1:64329`

Wait for the API and web containers to report healthy, then confirm the stack:

```bash
docker compose ps
uv run aq health
```

## First-run bootstrap

By default the API container auto-runs first-time setup on boot
(`AGENTICQUEUE_AUTO_SETUP=1`) and prints a one-time admin token to the API
logs. Capture that token immediately:

```bash
docker compose logs api
```

Look for a line like:

```text
[aq-init] One-time admin token: aq__...
```

Save that token right away. The stack only prints it once.

Export the token for CLI use:

```bash
export AGENTICQUEUE_TOKEN="aq__paste_the_token_here"
```

Now verify the install from the public CLI surface:

```bash
uv run aq whoami
uv run aq project list
uv run aq job list
```

Open the web UI at `http://127.0.0.1:3000`, paste the same token into the login
screen, and you should land in the read-only shell.

## Manual bootstrap variant

If you prefer to run first-time setup yourself instead of reading the API logs,
set `AGENTICQUEUE_AUTO_SETUP=0` in `.env` before `docker compose up -d`, then
run the public setup command once the API is healthy:

```bash
BOOTSTRAP_JSON="$(uv run aq setup)"
printf '%s\n' "$BOOTSTRAP_JSON"
export AGENTICQUEUE_TOKEN="$(
  printf '%s' "$BOOTSTRAP_JSON" \
    | uv run python -c "import json, sys; print(json.load(sys.stdin)['api_token'])"
)"
```

For local-only bootstrap debugging, the helper behind that flow is still
available as `uv run aq-local init`. It is not the public REST CLI surface.

## Restarting and shutting down

Normal restarts keep the existing workspace and do not mint a second token:

```bash
docker compose restart
```

To stop the stack without deleting data:

```bash
docker compose down
```

Do not use `docker compose down -v` unless you explicitly want to destroy the
database volume.

## Autonomous mode while keeping the UI

AgenticQueue follows ADR-AQ-003: the UI stays on in every deployment profile.
What changes is policy, not the container set.

Today the bundled default policy pack is `default-coding.policy.yaml`, which
ships with:

- `hitl_required: true`
- `autonomy_tier: 3`

If you want more autonomous execution later, keep the same compose stack and
switch to a policy pack that sets `hitl_required: false`. Do not remove the web
service or create a separate "headless" compose profile. The UI remains the
inspection and intervention surface even when approvals are relaxed.

## Production notes

- Put the API and web services behind a reverse proxy that terminates TLS.
- Replace the default `.env` credentials before exposing the stack anywhere:
  `POSTGRES_PASSWORD` and `AGENTICQUEUE_TOKEN_SIGNING_SECRET` should be real
  secrets.
- Back up the Postgres volume or schedule regular `pg_dump` exports before
  treating the instance as durable.
- Keep `.env` out of version control and rotate secrets before multi-user or
  internet-facing deployments.

## Verify release assets

Published release artifacts are keylessly signed by the GitHub Actions
`release.yml` workflow with Sigstore. Download an artifact and its matching
`.sigstore.json` bundle from the GitHub release page, then verify the blob
before unpacking it:

```bash
cosign verify-blob \
  --bundle agenticqueue-<version>.tar.gz.sigstore.json \
  --certificate-identity-regexp '^https://github.com/agenticqueue/agenticqueue/\.github/workflows/release.yml@refs/tags/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  agenticqueue-<version>.tar.gz
```

Verify the SBOM with the same identity and issuer:

```bash
cosign verify-blob \
  --bundle sbom.cdx.json.sigstore.json \
  --certificate-identity-regexp '^https://github.com/agenticqueue/agenticqueue/\.github/workflows/release.yml@refs/tags/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  sbom.cdx.json
```

If a release includes `sbom.node.cdx.json` or `grype-report.json`, verify each
one against its matching `.sigstore.json` bundle with the same flags.
