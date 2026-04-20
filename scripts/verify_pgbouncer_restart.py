"""Verify PgBouncer keeps backend connections stable across app restarts."""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

import httpx
import psycopg
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from agenticqueue_api.config import (
    get_psycopg_connect_args,
    get_sqlalchemy_sync_database_url,
)
from agenticqueue_api.seed import load_seed_fixture, seed_example_data

APP_DATABASE_URL = (
    "postgresql+asyncpg://agenticqueue:agenticqueue@127.0.0.1:64329/"
    "agenticqueue?prepared_statement_cache_size=0"
)
RAW_DATABASE_URL = "postgresql://agenticqueue:agenticqueue@127.0.0.1:54329/agenticqueue"
API_PORT = 18000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restarts", type=int, default=5)
    parser.add_argument("--max-server-connections", type=int, default=25)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    return parser.parse_args()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", *args],
        cwd=_repo_root(),
        check=True,
        text=True,
    )


def _repo_python() -> Path:
    python = _repo_root() / ".venv" / "Scripts" / "python.exe"
    if python.exists():
        return python
    raise RuntimeError("Expected repo virtualenv python at .venv\\Scripts\\python.exe")


def _wait_for_api_ready(timeout_seconds: float) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            response = httpx.get(
                f"http://127.0.0.1:{API_PORT}/openapi.json",
                timeout=1.0,
            )
            if response.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for FastAPI on 127.0.0.1:{API_PORT}")


def _seed_api_token() -> str:
    engine = sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        future=True,
        connect_args=get_psycopg_connect_args(),
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    fixture = load_seed_fixture()
    with session_factory() as session:
        result = seed_example_data(session, fixture)
        session.commit()
    engine.dispose()
    return result.api_token


def _count_backend_connections() -> int:
    with psycopg.connect(RAW_DATABASE_URL, connect_timeout=2) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*)
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND usename = current_user
                  AND pid <> pg_backend_pid()
                """)
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("Expected pg_stat_activity count row")
            count = row[0]
    return int(count)


def _exercise_db_path(api_token: str) -> None:
    response = httpx.get(
        f"http://127.0.0.1:{API_PORT}/v1/auth/tokens",
        headers={"Authorization": f"Bearer {api_token}"},
        timeout=10.0,
    )
    response.raise_for_status()


def _start_api_process() -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["AGENTICQUEUE_DATABASE_URL"] = APP_DATABASE_URL
    return subprocess.Popen(
        [
            str(_repo_python()),
            "-m",
            "uvicorn",
            "agenticqueue_api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(API_PORT),
        ],
        cwd=_repo_root(),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def _stop_api_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def main() -> int:
    args = parse_args()
    _compose("up", "-d", "db", "pgbouncer")
    _compose(
        "exec", "-T", "db", "pg_isready", "-U", "agenticqueue", "-d", "agenticqueue"
    )
    subprocess.run(
        [
            "uv",
            "run",
            "python",
            "apps/api/scripts/wait_for_db.py",
            "--attempts",
            "60",
            "--delay-seconds",
            "1",
        ],
        cwd=_repo_root(),
        check=True,
        text=True,
        env={**os.environ, "AGENTICQUEUE_DATABASE_URL": APP_DATABASE_URL},
    )
    subprocess.run(
        [
            "uv",
            "run",
            "alembic",
            "-c",
            "apps/api/alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=_repo_root(),
        check=True,
        text=True,
        env={**os.environ, "AGENTICQUEUE_DATABASE_URL": APP_DATABASE_URL},
    )
    api_token = _seed_api_token()

    observed: list[int] = []
    process = _start_api_process()
    try:
        _wait_for_api_ready(timeout_seconds=30.0)
        _exercise_db_path(api_token)
        observed.append(_count_backend_connections())

        for _ in range(args.restarts):
            _stop_api_process(process)
            time.sleep(args.delay_seconds)
            process = _start_api_process()
            _wait_for_api_ready(timeout_seconds=30.0)
            _exercise_db_path(api_token)
            observed.append(_count_backend_connections())
    finally:
        _stop_api_process(process)

    peak = max(observed)
    print(f"observed_backend_connections={observed}")
    print(f"peak_backend_connections={peak}")
    if peak > args.max_server_connections:
        raise SystemExit(
            f"PgBouncer restart-storm check failed: peak {peak} > {args.max_server_connections}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
