#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

if ! grep -q "apps/web/scripts/dev-start.sh" docker-compose.override.yml; then
  echo "AQ-310 failure: web dev command is not routed through the cache-busting startup script." >&2
  exit 1
fi

PROJECT_NAME="${AQ310_COMPOSE_PROJECT:-aq310-cache-bust}"
WEB_PORT="${AQ310_WEB_PORT:-3310}"
API_PORT="${AQ310_API_PORT:-8310}"
DB_PORT="${AQ310_DB_PORT:-55410}"
PGBOUNCER_PORT="${AQ310_PGBOUNCER_PORT:-65410}"

export AGENTICQUEUE_WEB_PORT="$WEB_PORT"
export AGENTICQUEUE_API_PORT="$API_PORT"
export AGENTICQUEUE_DB_PORT="$DB_PORT"
export AGENTICQUEUE_PGBOUNCER_PORT="$PGBOUNCER_PORT"
export AQ_ADMIN_EMAIL="${AQ_ADMIN_EMAIL:-admin@localhost}"

compose=(docker compose -p "$PROJECT_NAME" -f docker-compose.yml -f docker-compose.override.yml)

cleanup() {
  "${compose[@]}" down --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

cleanup

mkdir -p apps/web/.next
printf '{"stale":true}\n' > apps/web/.next/build-manifest.json
printf 'stale chunk sentinel\n' > apps/web/.next/aq310-stale-sentinel
touch -t 200001010000 apps/web/.next/build-manifest.json apps/web/.next/aq310-stale-sentinel
touch apps/web/package.json

"${compose[@]}" up -d --build web

status=""
for _ in {1..90}; do
  status="$(curl -sS -o /dev/null -w "%{http_code}" "http://127.0.0.1:${WEB_PORT}/" || true)"
  if [[ "$status" == "200" || "$status" == "307" || "$status" == "308" ]]; then
    break
  fi
  sleep 2
done

if [[ "$status" != "200" && "$status" != "307" && "$status" != "308" ]]; then
  "${compose[@]}" ps >&2 || true
  "${compose[@]}" logs --tail=160 web >&2 || true
  echo "AQ-310 failure: expected GET / to return 200, 307, or 308; got ${status:-no response}." >&2
  exit 1
fi

if [[ -e apps/web/.next/aq310-stale-sentinel ]]; then
  "${compose[@]}" logs --tail=160 web >&2 || true
  echo "AQ-310 failure: stale .next sentinel survived web startup." >&2
  exit 1
fi

if ! "${compose[@]}" logs web | grep -q "AQ dev startup: removed stale .next cache"; then
  "${compose[@]}" logs --tail=160 web >&2 || true
  echo "AQ-310 failure: cache-bust startup log line was not emitted." >&2
  exit 1
fi

echo "AQ-310 cache bust verified: GET / returned $status and stale .next was removed."
