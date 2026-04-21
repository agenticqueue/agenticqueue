# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

COPY pyproject.toml uv.lock README.md ./
COPY apps/api ./apps/api
COPY apps/cli ./apps/cli
COPY config ./config
COPY docs/surface-1.0.md ./docs/surface-1.0.md
COPY policies ./policies
COPY schemas ./schemas
COPY task_types ./task_types

RUN uv sync --frozen --no-dev

FROM builder AS dev

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 8000

FROM python:3.12-slim AS runner

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp/agenticqueue \
    TMPDIR=/tmp/agenticqueue \
    XDG_CACHE_HOME=/tmp/agenticqueue/.cache \
    AGENTICQUEUE_REPO_ROOT=/workspace \
    AGENTICQUEUE_TASK_TYPES_DIR=/workspace/task_types \
    AGENTICQUEUE_POLICIES_DIR=/workspace/policies

WORKDIR /workspace

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /tmp/agenticqueue --shell /usr/sbin/nologin app \
    && mkdir -p /tmp/agenticqueue /workspace \
    && chown -R app:app /tmp/agenticqueue /workspace

COPY --from=builder --chown=app:app /opt/venv /opt/venv
COPY --from=builder --chown=app:app /workspace/apps/api ./apps/api
COPY --from=builder --chown=app:app /workspace/apps/cli ./apps/cli
COPY --from=builder --chown=app:app /workspace/config ./config
COPY --from=builder --chown=app:app /workspace/docs/surface-1.0.md ./docs/surface-1.0.md
COPY --from=builder --chown=app:app /workspace/policies ./policies
COPY --from=builder --chown=app:app /workspace/schemas ./schemas
COPY --from=builder --chown=app:app /workspace/task_types ./task_types
COPY --from=builder --chown=app:app /workspace/pyproject.toml ./pyproject.toml
COPY --from=builder --chown=app:app /workspace/README.md ./README.md

USER app

EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=5s --start-period=5s --retries=12 CMD python -c "import sys,urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=2).read(); sys.exit(0)"

# Compose can reuse this image for the future worker service by overriding CMD.
CMD ["python", "-m", "uvicorn", "agenticqueue_api.app:app", "--host", "0.0.0.0", "--port", "8000"]
