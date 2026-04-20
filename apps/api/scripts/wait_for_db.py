"""Wait until the configured Postgres database accepts connections."""

from __future__ import annotations

import argparse
import time

import psycopg

from agenticqueue_api.config import get_psycopg_connect_args, get_sync_database_url


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempts", type=int, default=30)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sync_url = get_sync_database_url()

    for attempt in range(1, args.attempts + 1):
        try:
            prepare_threshold = get_psycopg_connect_args()["prepare_threshold"]
            with psycopg.connect(
                sync_url,
                connect_timeout=2,
                prepare_threshold=prepare_threshold,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            return 0
        except psycopg.OperationalError:
            if attempt == args.attempts:
                break
            time.sleep(args.delay_seconds)

    raise SystemExit(
        f"Database did not become ready after {args.attempts} attempts: {sync_url}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
