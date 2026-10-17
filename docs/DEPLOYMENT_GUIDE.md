# ROOT 2.0 (Biztective) — Production Deployment & Redeployment Guide

This document explains the live production deployment of **ROOT 2.0 (Biztective)** on **Alternate Futures (`Alternate Clouds`)**, every component and configuration used, and the exact step-by-step workflow to redeploy when you make backend, frontend, database, or environment changes.

---

## 1. Live Production Endpoints & Service Identifiers

| Component | Public HTTPS URL | Alternate Futures Service Name | Service ID | Active Port |
|---|---|---|---|---|
| **Frontend (`apps/web`)** | `https://root-web-app.alternatefutures.ai` | `root-web` | `cmvbx1b1016uebs1u7l6ge6p7` | `3000` |
| **Backend (`services/api`)** | `https://root-api-app.alternatefutures.ai` | `root-api` | `cmvbwfap014pibs1u7pxz2e5k` | `8000` |
| **Same-Origin API Proxy** | `https://root-web-app.alternatefutures.ai/api/v1/*` | *(Proxied by `apps/web/src/proxy.ts`)* | — | `443 -> 8000` |
| **Health & Readiness** | `https://root-web-app.alternatefutures.ai/api/v1/readyz` | `root-api` via `root-web` | — | HTTP `200` |

- **Alternate Futures Organization ID:** `u3VLoOrBKeXnLUvVyDpAt`
- **Alternate Futures Project:** `root-2` (`cmvbvc76110gwbs1uxtk23lhr`)
- **CLI Tool:** `@alternatefutures/acc` (`v1.10.0`)

---

## 2. Architecture & Everything Used in Production

```
Browser (HTTPS)
   │
   ▼
https://root-web-app.alternatefutures.ai  (Alternate Futures Container: apps/web, Node 24, UID 10001)
   │  ├─ Renders Next.js 16 App Router pages with per-request CSP nonces
   │  └─ Runtime proxy (apps/web/src/proxy.ts) forwards /api/v1/* ──┐
   │     keeping __Host-root_session & __Host-root_csrf first-party │
   ▼                                                                ▼
https://root-api-app.alternatefutures.ai  (Alternate Futures Container: services/api, Python 3.13, UID 10001)
   │  ├─ FastAPI REST API + Embedded PostgreSQL Lease Worker (--workers 1)
   │  ├─ LangGraph Investigation & Recovery Subgraphs (durable PostgresSaver)
   │  └─ Startup Entrypoint (docker-entrypoint.sh): runs `alembic upgrade head`
   │
   ├───────────────────────────────┬────────────────────────────────┐
   ▼                               ▼                                ▼
Supabase Managed PostgreSQL    Google Sheets Bridge             LLM Provider
(TLS sslmode=require)          (Apps Script Web App)            (Configurable: disabled /
- Runtime role: `root_app`     - Ownership verification         openai / anthropic)
  (NOBYPASSRLS, RLS enforced)  - Idempotent recovery rows
- Migration role: `postgres`   - Read-back verification
- `langgraph.*` checkpoints
```

### Key Files Created for Deployment

1. **`services/api/Dockerfile` & `services/api/docker-entrypoint.sh`:**
   - Multi-stage Python build (`python:3.13-slim-bookworm`) installing locked production dependencies from `services/api/uv.lock` (`uv sync --frozen --no-dev`).
   - Runs as non-root user `rootapp` (`UID 10001`, `GID 10001`).
   - On container start, `docker-entrypoint.sh` verifies required secrets (`DATABASE_URL`, `SESSION_SECRET`, `FRONTEND_ORIGIN`), runs `alembic upgrade head` using `DATABASE_URL_MIGRATIONS` (when `RUN_MIGRATIONS=1`), optionally runs `python -m app.cli seed-demo` (when `SEED_DEMO_ON_START=1`), and execs `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips="*"`.
2. **`apps/web/Dockerfile` & `apps/web/src/proxy.ts`:**
   - Multi-stage Node 24 build (`node:24.14.1-alpine3.21`) producing a standalone Next.js 16 production server running as non-root user `rootapp` (`UID 10001`, `GID 10001`).
   - `apps/web/src/proxy.ts` intercepts `/api/v1/:path*` at **request time** using the container's runtime `BACKEND_URL` environment variable (`https://root-api-app.alternatefutures.ai`), so changing backend URLs never requires rebuilding the frontend image.
3. **`.github/workflows/deploy-images.yml`:**
   - GitHub Actions workflow (`ubuntu-24.04`) that builds both production images, validates non-root execution (`UID 10001`), checks that no `.env` files are inside the image layers, verifies fail-closed startup and health/security headers, and pushes versioned OCI tags to `ttl.sh`.
4. **Database & Security Configuration:**
   - **Supabase PostgreSQL** (`aws-0-ap-southeast-2.pooler.supabase.com:5432/postgres?sslmode=require`):
     - `DATABASE_URL`: Connects as `root_app` (`NOSUPERUSER`, `NOBYPASSRLS`, not table owner) so PostgreSQL Row-Level Security (RLS) is strictly enforced on all 22 tenant tables and verified by `/api/v1/readyz`.
     - `DATABASE_URL_MIGRATIONS`: Connects as `postgres` (table owner) exclusively for `alembic upgrade head` during container startup.
   - **Production Cookies & Origin Protection:**
     - `ENVIRONMENT=production` activates `__Host-root_session` and `__Host-root_csrf` (`Secure; HttpOnly; SameSite=Strict/Lax; Path=/`).
     - `FRONTEND_ORIGIN=https://root-web-app.alternatefutures.ai` enforces strict CORS and CSRF `Origin` validation on all mutating `/api/v1/*` requests.

---

## 3. How to Redeploy When You Make Changes

> **Important Alternate Futures Rule on Image Tags:**
> Alternate Futures (`Alternate Clouds`) rejects `:latest` and `:main` tags and caches image tags on compute nodes. **Every time you redeploy with new code, bump the version number in the image tag** (for example, `1.0.2:24h` → `1.0.3:24h`).

### Workflow A: Redeploy Using GitHub Actions (Recommended — No Local Docker Needed)

Use this workflow whenever you edit backend or frontend code and push to GitHub:

#### Step 1: Bump the image version tag in `.github/workflows/deploy-images.yml`
Update the version number (e.g. `1.0.2` → `1.0.3`) for `ttl.sh/root2-mahateer-api-<VERSION>:24h` and `ttl.sh/root2-mahateer-web-<VERSION>:24h` in `.github/workflows/deploy-images.yml`.

#### Step 2: Commit and push to `main`
```bash
git add -A
git commit -m "feat: your change description"
git push origin main
```
*(Wait ~2 minutes for the **Container Build, Test & Publish** workflow in GitHub Actions to finish pushing the new tags to `ttl.sh`.)*

#### Step 3: Deploy the new image tag to Alternate Futures
Run the following command(s) in your terminal:

- **If you changed Frontend (`apps/web`) code:**
  ```bash
  acc services deploy cmvbx1b1016uebs1u7l6ge6p7 --image ttl.sh/root2-mahateer-web-1.0.3:24h -y
  ```

- **If you changed Backend (`services/api`) code or Alembic migrations:**
  ```bash
  acc services deploy cmvbwfap014pibs1u7pxz2e5k --image ttl.sh/root2-mahateer-api-1.0.3:24h -y
  ```

#### Step 4: Verify the live deployment
```bash
curl -sS https://root-web-app.alternatefutures.ai/api/v1/readyz
# Expected output: {"status":"ready"}
```

---

### Workflow B: Redeploy Directly from Your Laptop (When Docker Desktop is Running)

If Docker Desktop is running locally on your Mac, you can build, push, and redeploy in 60 seconds without waiting for GitHub Actions:

```bash
# 1. Pick a new version tag (e.g. 1.0.3)
export TAG="1.0.3:24h"

# 2. If Backend changed: build (linux/amd64), push, and deploy
docker build --platform linux/amd64 -t ttl.sh/root2-mahateer-api-$TAG services/api
docker push ttl.sh/root2-mahateer-api-$TAG
acc services deploy cmvbwfap014pibs1u7pxz2e5k --image ttl.sh/root2-mahateer-api-$TAG -y

# 3. If Frontend changed: build (linux/amd64), push, and deploy
docker build --platform linux/amd64 -t ttl.sh/root2-mahateer-web-$TAG apps/web
docker push ttl.sh/root2-mahateer-web-$TAG
acc services deploy cmvbx1b1016uebs1u7l6ge6p7 --image ttl.sh/root2-mahateer-web-$TAG -y
```

---

## 4. Common Operations Cheat Sheet

### Change an Environment Variable (e.g., Enable Live OpenAI / Anthropic LLM)
Environment variables are stored encrypted on Alternate Futures per service. After updating an environment variable, redeploy the service so the container restarts with the new value:

```bash
# Example: Switch backend from deterministic mode to OpenAI
acc services env set cmvbwfap014pibs1u7pxz2e5k \
  LLM_PROVIDER=openai \
  LLM_MODEL=gpt-4o-mini \
  LLM_API_KEY="your-api-key"

# Apply the updated environment variables by redeploying the current image
acc services deploy cmvbwfap014pibs1u7pxz2e5k --image ttl.sh/root2-mahateer-api-1.0.2:24h -y
```

### View Live Container Logs
```bash
# Backend API & Worker logs (structured JSON)
acc services logs cmvbwfap014pibs1u7pxz2e5k

# Frontend Next.js logs
acc services logs cmvbx1b1016uebs1u7l6ge6p7
```

### Inspect Service Status & Resources
```bash
acc services list
acc services info cmvbwfap014pibs1u7pxz2e5k
acc services info cmvbx1b1016uebs1u7l6ge6p7
```

### Database Migrations on Redeploy
- Because `RUN_MIGRATIONS=1` is set on `root-api`, any new Alembic migration inside `services/api/alembic/versions/` is applied automatically (`alembic upgrade head`) using `DATABASE_URL_MIGRATIONS` before Uvicorn starts accepting traffic.
- Always write expand → migrate → contract migrations (`AGENTS.md R-14`) so rolling back a container image remains compatible with the database schema.

### Instant Rollback to a Previous Release
If a new deployment has a bug, roll back immediately by deploying the previous known-good image tag:
```bash
# Roll back backend to 1.0.0
acc services deploy cmvbwfap014pibs1u7pxz2e5k --image ttl.sh/root2-mahateer-api-1.0.0:24h -y

# Roll back frontend to 1.0.1
acc services deploy cmvbx1b1016uebs1u7l6ge6p7 --image ttl.sh/root2-mahateer-web-1.0.1:24h -y
```

### Run End-to-End Playwright Tests Against the Live Deployment
```bash
cd apps/web
PLAYWRIGHT_REMOTE_URL=https://root-web-app.alternatefutures.ai \
  npx playwright test
```

