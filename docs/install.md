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

Before first boot, set `AQ_ADMIN_PASSCODE` in `.env`. The setup page uses that
passcode to claim the first local owner account.

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

Open the web UI at `http://127.0.0.1:3000` and follow the first-run setup flow.
The setup form asks for:

- email
- `AQ_ADMIN_PASSCODE`
- password
- password confirmation

The API returns the first admin API token once after setup. It starts with
`aq_live_`; save it immediately because the database stores only its hash.

Export that token for CLI use:

```bash
export AGENTICQUEUE_TOKEN="aq_live_paste_the_token_here"
```

Now verify the install from the public CLI surface:

```bash
uv run aq whoami
uv run aq project list
uv run aq job list
```

After setup, use the login screen with the email and password you created.

## Manual bootstrap variant

If you prefer to run first-time setup through HTTP, call the bootstrap endpoint
once the API is healthy:

```bash
BOOTSTRAP_JSON="$(
  curl -fsS http://127.0.0.1:8000/api/auth/bootstrap_admin \
    -H 'Content-Type: application/json' \
    -d '{"email":"admin@localhost","passcode":"'"$AQ_ADMIN_PASSCODE"'","password":"CorrectHorse12!"}'
)"
export AGENTICQUEUE_TOKEN="$(
  printf '%s' "$BOOTSTRAP_JSON" \
    | uv run python -c "import json, sys; print(json.load(sys.stdin)['first_token'])"
)"
```

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

AgenticQueue keeps the UI on in every deployment profile. What changes is
policy, not the container set.

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
