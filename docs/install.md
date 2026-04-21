# Install

AgenticQueue currently supports a source-first install for local development:

```bash
git clone https://github.com/agenticqueue/agenticqueue.git
cd agenticqueue
cp .env.example .env
uv sync
docker compose up -d db pgbouncer
uv run alembic -c apps/api/alembic.ini upgrade head
```

The full single-command Docker install is tracked in Phase 10. Until that lands,
use the source-first flow above for local setup and [`docs/dev-setup.md`](./dev-setup.md)
for contributor tooling.

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
