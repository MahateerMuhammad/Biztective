# ROOT 2.0

Autonomous business investigation and recovery platform (NaSCon Agentika 2.0).
Specifications live in [docs/](docs/): start with [ROOT_MASTER_PLAN.md](docs/ROOT_MASTER_PLAN.md).
Coding agents must follow [AGENTS.md](AGENTS.md).

| Path | What |
|---|---|
| `services/api` | FastAPI backend (Python 3.14, uv, SQLAlchemy, Alembic) |
| `apps/web` | Next.js frontend (Node 24, TypeScript, Tailwind CSS, shadcn/ui) |
| `infra/postgres` | Database role provisioning |
| `docker-compose.yml` | Local PostgreSQL 18 only |

## Local development

Prerequisites: Docker (or PostgreSQL 18), [uv](https://docs.astral.sh/uv/), Node.js 24 (`apps/web/.nvmrc`).

```bash
# 1. Database: set two random passwords, then start PostgreSQL (creates the root_app role).
cp .env.example .env            # fill POSTGRES_PASSWORD and ROOT_APP_DB_PASSWORD
docker compose up -d --wait

# 2. Backend
cd services/api
cp .env.example .env            # fill the CHANGE_ME passwords, SESSION_SECRET and DEMO_* values
uv sync --locked
uv run alembic upgrade head     # runs as the owner role (DATABASE_URL_MIGRATIONS)
uv run python -m app.cli seed-demo
uv run uvicorn app.main:create_app --factory --port 8000

# 3. Frontend (new terminal)
cd apps/web
cp .env.example .env.local
npm ci
npm run dev                     # http://localhost:3000, sign in with the DEMO_* account
```

The browser only talks to the Next.js origin. `/api/v1/*` is rewritten to the backend, so session
cookies stay first-party. The web process connects to PostgreSQL as `root_app`, a role with row-level
security and no DDL rights; `/api/v1/readyz` reports not-ready if it is pointed at a privileged role.

## Quality gates

```bash
# Backend (needs TEST_DATABASE_ADMIN_URL and TEST_APP_DB_PASSWORD, see services/api/.env.example)
cd services/api
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest --cov --cov-fail-under=85
uv run pip-audit

# Frontend
cd apps/web
npm run lint && npm run typecheck && npm run test:coverage && npm run build
npm run audit:gate
npx playwright test             # needs the backend env vars plus E2E_EMAIL / E2E_PASSWORD
```

CI (`.github/workflows/ci.yml`) runs all of the above plus an OpenAPI/TypeScript contract drift
check and a full-history gitleaks scan.
