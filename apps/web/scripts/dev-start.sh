#!/bin/sh
set -eu

WEB_DIR="${AQ_WEB_DIR:-/workspace/apps/web}"

if [ "${AQ_DEV_MODE:-0}" = "1" ] && [ "${NODE_ENV:-}" = "development" ]; then
  if [ -d "$WEB_DIR/.next" ]; then
    rm -rf "$WEB_DIR/.next"
    echo "AQ dev startup: removed stale .next cache at $WEB_DIR/.next"
  fi
fi

if [ "$#" -eq 0 ]; then
  set -- npm run dev --workspace=@agenticqueue/web -- --hostname 0.0.0.0 --port 3000
fi

exec "$@"
