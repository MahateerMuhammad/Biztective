#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL must be set}"
: "${SESSION_SECRET:?SESSION_SECRET must be set}"
: "${FRONTEND_ORIGIN:?FRONTEND_ORIGIN must be set}"

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    DATABASE_URL_MIGRATIONS="${DATABASE_URL_MIGRATIONS:-$DATABASE_URL}" alembic upgrade head
fi

if [ "${SEED_DEMO_ON_START:-0}" = "1" ]; then
    DATABASE_URL_MIGRATIONS="${DATABASE_URL_MIGRATIONS:-$DATABASE_URL}" python -m app.cli seed-demo
fi

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# Single worker process so the embedded PostgreSQL job worker (WORKER_ENABLED=true)
# and in-process SEC-20 session token bucket operate within one instance (ARCHITECTURE.md).
exec uvicorn app.main:create_app \
    --factory \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --workers 1 \
    --proxy-headers \
    --forwarded-allow-ips="*"
