# syntax=docker/dockerfile:1
FROM python:3.14-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

RUN pip install --no-cache-dir uv==0.12.5

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.14-slim-bookworm AS runtime

RUN groupadd --gid 10001 rootapp \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin rootapp

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app" \
    PORT=8000

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=10001:10001 alembic.ini ./alembic.ini
COPY --chown=10001:10001 migrations ./migrations
COPY --chown=10001:10001 app ./app
COPY --chown=10001:10001 scripts ./scripts
COPY --chown=10001:10001 docker-entrypoint.sh ./docker-entrypoint.sh

RUN chmod 0755 /app/docker-entrypoint.sh

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/api/v1/healthz', timeout=4)"]

ENTRYPOINT ["/app/docker-entrypoint.sh"]
