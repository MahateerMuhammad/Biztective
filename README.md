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
uv run --env-file .env alembic upgrade head   # owner role (DATABASE_URL_MIGRATIONS from .env)
uv run --env-file .env python -m app.cli seed-demo
uv run uvicorn app.main:create_app --factory --port 8000

# 3. Frontend (new terminal)
cd apps/web
cp .env.example .env.local
npm ci
npm run dev                     # http://localhost:3000, sign in with the DEMO_* account
```

Then open **Upload dataset** on the business page and select `orders.csv`, `products.csv`,
`customers.csv` and `returns.csv`. Uncertain column matches stop for your confirmation. The data
quality report shows every rejected row with its rule (V-xxx) and reason. To generate realistic
files:

```bash
cd services/api
uv run python -m scripts.generate_dataset --seed 7 --out /tmp/root-demo   # add --noise 0.02 / --pii-column
```

After validation, ROOT analyses each ready dataset automatically. Open **View analytics** for:
- the equal-period comparison, product return rates and customer lifecycle segments
- deterministic incidents, each with its source-record evidence
- any data limitations (for example missing costs) that make a figure unavailable

From the analytics page, **Start investigation** opens the Investigation Workspace. ROOT
investigates every incident with read-only evidence tools, drafts findings (version 1) and then
pauses durably on the single most useful question the data cannot answer, for example whether a
discount rise was a planned clearance. An owner or analyst answers with an option and optional
free text; the answer must quote the paused checkpoint, is stored with its author, and resumes the
exact LangGraph checkpoint. ROOT re-evaluates the affected hypotheses, investigates the
alternatives the answer points to, and records a new version. **Investigation replay** compares
any two versions: which findings, hypotheses, root causes and recommendations changed, and that
every original measurement is unchanged. At most 3 questions per incident and 6 per
investigation are asked. The APIs are under `/api/v1/businesses/{business_id}/investigations`
(see DATA_CONTRACTS §12–§13); the workflow checkpoints to PostgreSQL after every node. Recovery
actions are not part of this phase.

Investigations run deterministically by default. For Anthropic, set `LLM_PROVIDER=anthropic` and
`ANTHROPIC_API_KEY` in the backend environment; for Groq (AMD-16), set `LLM_PROVIDER=groq` and
`GROQ_API_KEY` (optional `GROQ_MODEL`, default `openai/gpt-oss-120b`). Model output cannot change analytical facts or run
external actions. `LLM_MAX_CALLS`, `LLM_MAX_TOKENS`, `LLM_DEADLINE_SECONDS` and
`INVESTIGATION_TIMEOUT_SECONDS` bound execution. To run the separate live integration test:

```bash
ROOT_LIVE_LLM_TESTS=1 uv run pytest tests/investigation/test_live_model.py -v
```

It also requires the normal test database variables and an `ANTHROPIC_API_KEY`; add
`ROOT_LIVE_LLM_PROVIDER=groq` to run it against Groq with `GROQ_API_KEY`. Default tests use
scripted model responses and do not call the provider.

The browser only talks to the Next.js origin. `/api/v1/*` is rewritten to the backend, so session
cookies stay first-party. The web process connects to PostgreSQL as `root_app`, a role with row-level
security and no DDL rights; `/api/v1/readyz` reports not-ready if it is pointed at a privileged role.

## Quality gates

```bash
# Backend (needs TEST_DATABASE_ADMIN_URL and TEST_APP_DB_PASSWORD, see services/api/.env.example)
cd services/api
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest --cov --cov-fail-under=85
uv run pytest -m perf -s        # uploads/analysis through 500k lines; 20-user API concurrency
uv run pip-audit

# Frontend
cd apps/web
npm run lint && npm run typecheck && npm run test:coverage && npm run build
npm run audit:gate
npx playwright test             # needs the backend env vars plus E2E_EMAIL / E2E_PASSWORD and uv
```

CI (`.github/workflows/ci.yml`) runs all of the above plus an OpenAPI/TypeScript contract drift
check and a full-history gitleaks scan.
