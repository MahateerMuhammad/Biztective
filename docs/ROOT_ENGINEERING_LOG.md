# ROOT 2.0 — Engineering Log

> The single cumulative engineering journal. **Append-only.** Each phase appends one section using the template below.
> Earlier sections are never rewritten. The only permitted edit is filling in a commit hash or push status that the immediately previous phase left as *pending*, and that edit must be noted (AGENTS.md R-4).

## Section template

```
## Phase <n> — <name>
- Date:
- Objective and scope:
- Technical decisions and reasons:
- Files created / changed / removed:
- Implemented functionality:
- Code flow and execution flow:
- API and database changes:
- Security controls:
- Performance considerations:
- Tests executed and actual results: (commands, counts, coverage, CI URL)
- Errors encountered and fixes applied:
- Known limitations and remaining risks:
- Acceptance criteria status: (each AC with evidence)
- Commit hash and GitHub push status:
- Next-phase dependencies:
```

---

## Phase 1 — Documentation and architecture

- **Date:** 2026-10-16

### Objective and scope

Define the complete specification and architecture of ROOT 2.0 before any implementation: product vision, personas, requirements, phase plan, data and API contracts, security model, test strategy, demo plan, and mandatory agent rules. Per the phase objective, no application code, dependencies, endpoints or scaffolding were created.

### Repository inspection (before work)

| Item | Finding |
|---|---|
| Working tree | `/Users/apple/Desktop/ROOT` contained only `.git/` and no files |
| Branch | `main`, no commits yet |
| Remote | `origin` = `https://github.com/MahateerMuhammad/ROOT.git` |
| Remote state | `git ls-remote --heads origin` succeeded (exit 0) and returned no branches, so the remote is reachable and empty |
| Credentials | `credential.helper=osxkeychain`. No credentials were read or printed |
| Git identity | Repository owner's configured identity. No AI co-author trailers are used (user instruction, recorded in AGENTS.md §3) |
| Toolchain (informational, not installed by this phase) | `uv` 0.12.5, Python 3.14.8, Node 20.19.6 available locally |

### Technical decisions and reasons

The 19 ADRs are recorded in `docs/ARCHITECTURE.md §15`. The principal decisions:

1. **Modular monolith** (ADR-001) and **PostgreSQL as the only store** (ADR-002), with a **Postgres-backed job queue** and an in-process worker (ADR-003). These satisfy "one backend, one frontend, no Redis or Kafka" while giving durable, transactional background work.
2. **Deterministic facts, LLM interpretation only** (ADR-004). The LLM output contract forbids digits and entity names outside server-rendered placeholders, so no LLM-generated number can be shown as a financial fact.
3. **Money in SQL NUMERIC** (ADR-005). pandas float is used only for robust statistics.
4. **Immutable datasets + overlays + revisions** (ADR-007). Human corrections never rewrite evidence. They create a new, comparable revision.
5. **Event log + projection replay** (ADR-008). Replay never re-runs analysis or the LLM.
6. **Execution outside the agent graph with payload-hash-bound owner approval** (ADR-009). Rejected actions have no execution path.
7. **Same-origin proxy + server-side sessions** (ADR-010) and **app scoping + RLS** (ADR-011) for tenant isolation.
8. **Google Sheets via service account with an ownership verification code** (ADR-012). This avoids storing user OAuth tokens and prevents cross-tenant sheet binding.
9. **Bounded hypothesis and strategy catalogs** (ADR-014). Every hypothesis has deterministic verification criteria.
10. **Provider choice and dependency versions deferred** to Phases 7 and 2 respectively (ADR-015, ADR-016). They are verified against current official sources at that time rather than guessed now.

### Files created

| File | Content |
|---|---|
| `docs/ROOT_MASTER_PLAN.md` | Vision, personas, use cases, journeys, scope and non-goals, FR-01–29, NFR-01–10, judging criteria, 13 phases with prompts, scope, tests and acceptance criteria, dependency graph, traceability matrix, coverage report, amendments section |
| `docs/ROOT_ENGINEERING_LOG.md` | This journal and its template |
| `docs/ARCHITECTURE.md` | Context, components, runtime and job queue, lifecycle, detection algorithm, hypothesis catalog, evidence tools, verification, HITL (triggers, corrections, persistence and resume table), LLM gateway and validation, replay, recovery planning, action state machine, idempotency, integrations, LangGraph state and transitions, stack, deployment and rollback, ADR-001–019, performance, risks |
| `docs/DATA_CONTRACTS.md` | CSV contracts, validation rules V-001–V-099, accounting assumptions A1–A11, metrics M01–M21, dimensions, `analysis_config` v1, RFM segmentation and risk flags, revision hashing, database schema, HTTP API v1, RFC 9457 errors, event types, LLM I/O schemas and placeholder grammar |
| `docs/SECURITY_MODEL.md` | Assets, trust boundaries, SEC-01–25, sessions, CSRF, role-permission matrix, RLS design, headers, STRIDE threat model TH-01–22, audit, retention, secrets, review checklist, residual risks |
| `docs/TEST_STRATEGY.md` | Principles, levels, environments, quality gates, test catalog T-01–54, generator and anti-hardcoding, CI pipeline, evidence recording |
| `docs/DEMO_PLAN.md` | Six-minute script with 14 steps mapped to APIs and requirements, timing, branches and fallbacks, rehearsal checklist |
| `AGENTS.md` | Rules R-1–R-17, git policy, phase completion procedure |

No files were changed or removed (the repository was empty).

### Implemented functionality

None (documentation phase).

### Code flow and execution flow

Specified, not implemented: see ARCHITECTURE §4.5 (end-to-end flow) and §12.2 (graph transitions).

### API and database changes

Specified only: DATA_CONTRACTS §6 (schema) and §7 (API). No migrations exist yet.

### Security controls

Specified only: SECURITY_MODEL §3 (SEC-01–25). Each has a test (§10.3 matrix).

### Performance considerations

Targets in NFR-02 and NFR-08. Design measures are in ARCHITECTURE §16. Partitioning, caching and replicas are deferred until measured.

### Tests executed and actual results

No code tests exist in this phase. The following **document validation** was executed with Python scripts run against the documents in this session:

| Check | Command or method | Actual result |
|---|---|---|
| Every FR, NFR and SEC appears in the traceability matrix | regex over the master plan §10 | 29/29 FR, 10/10 NFR, 25/25 SEC present |
| Every referenced test ID is defined | regex over all docs vs the TEST_STRATEGY §5 table | 54 defined, 0 undefined references, 0 orphan tests |
| Every referenced SEC and ADR is defined | regex | 25 SEC defined, 0 undefined. 19 ADRs defined, 0 undefined |
| Metric definitions | regex `M\d\d` in the DATA_CONTRACTS §3.3 table | 21 metrics, each with formula, period basis and undefined rule |
| Acceptance criteria per phase | regex `AC-<n>.<m>` | every phase 1–13 has ≥ 4 measurable criteria |
| Matrix vs test-catalog coverage agreement | each matrix row's tests list that requirement in their Covers column | **6 mismatches found first run**, all fixed, re-check below |

### Errors encountered and fixes applied

1. **Matrix and test-catalog mismatches (6):** FR-01/T-13, FR-04/T-10, FR-08/T-06, FR-08/T-22, FR-21/T-34 and FR-27/T-44 were mapped in the matrix but not listed in those tests' Covers column. Fix: updated the TEST_STRATEGY Covers column. T-06 now explicitly tests hypothesis applicability, and T-22 now also tests rejection of LLM-proposed templates outside the catalog.
2. **Metric count:** the coverage report initially stated 19 metrics. The final contract defines 21 (M01–M21). Corrected.
3. **Placeholder grammar:** ARCHITECTURE said `<json_path>` while DATA_CONTRACTS said `<field>`. Unified to `<field>`.
4. **Human-input carry-over across revisions:** clarifications were keyed by per-revision `incident_id`, so a correction (new revision → new incident rows) would have re-asked answered questions. Fix: clarifications are unique by `(investigation_id, incident_key, criterion_key)`, and ARCHITECTURE §7.4 now states that human inputs carry over by incident key and template or criterion key. T-27 asserts this.
5. **Demo dataset noise:** DEMO_PLAN relied on a generator `--noise` option not listed in TEST_STRATEGY §6. Added.

The checks were re-run after the fixes: 0 mismatches, 0 undefined references.

### Requirements coverage report

See ROOT_MASTER_PLAN §11. Summary:

| Validation item | Status |
|---|---|
| Every feature has an implementation phase | Pass |
| Every security requirement has a test strategy | Pass |
| Every numerical metric has an explicit calculation definition | Pass (M01–M21, A1–A11, config v1) |
| Every HITL checkpoint has a persistence and resume strategy | Pass (ARCHITECTURE §7.7, 4 interrupt types) |
| Every external action has authorization and idempotency requirements | Pass (ARCHITECTURE §10.4, §10.5, §11.3; 4 action types) |
| Every phase has measurable acceptance criteria | Pass |
| Every demo action is achievable through the architecture | Pass (DEMO_PLAN §4, 14 steps mapped to endpoints) |

### Known limitations and remaining risks

| Risk | Impact | Mitigation and owner |
|---|---|---|
| Detection thresholds (config v1) are uncalibrated | False positives or negatives | Calibrate in Phase 6 against AC-6.1. Changes create config v2 by amendment |
| LLM provider not yet selected | Structured-output and tool-calling behaviour unverified | Phase 7 verification and amendment (ADR-016). The deterministic fallback guarantees completion |
| Dependency versions not pinned | Compatibility unknown | Phase 2 pins against current registries (ADR-015) |
| Worker shares the API process | CPU contention at large datasets | Measured in Phase 12. Same-image second process as the escape hatch (by amendment) |
| Static product cost (A4) | Margin attribution limits | Stated in the UI and DATA_CONTRACTS |
| Slack webhooks are not idempotent | Possible duplicate on ambiguous timeout | `outcome_unknown` with human resolution. Slack is optional |
| Six-minute demo depends on live services | Demo failure | Deterministic mode, retry flows, recorded fallback (DEMO_PLAN §6) |
| No MFA or SSO | Account takeover risk | Throttling and Argon2id. Out of scope (NG-09) |

### Acceptance criteria status

| AC | Status | Evidence |
|---|---|---|
| AC-1.1 Eight files exist and are committed | Met on commit (see below) | file list above |
| AC-1.2 Matrix completeness | Met | validation table |
| AC-1.3 Metric definitions complete | Met | DATA_CONTRACTS §3.3 |
| AC-1.4 HITL persistence and resume | Met | ARCHITECTURE §7.7 |
| AC-1.5 Action authorization and idempotency | Met | ARCHITECTURE §10.5, §11 |
| AC-1.6 Measurable phase criteria | Met | master plan §8 |
| AC-1.7 Demo steps mapped | Met | DEMO_PLAN §4 |

### Commit hash and GitHub push status

- **Phase 1 commit:** `0a96153d98aa48bf923b4209942b6a7de1589245` (`docs: Phase 1 specification and architecture for ROOT 2.0`). Author: the repository owner's git identity, with no AI co-author trailer.
- **Push:** `git push -u origin main` created the new branch `main` on `origin`. Verified with `git ls-remote origin refs/heads/main` = `0a96153d98aa48bf923b4209942b6a7de1589245`, which matches local `HEAD`.
- **Bookkeeping commit:** this hash and push record were added in a follow-up `docs:` commit. Its hash and push verification are recorded at the start of the Phase 2 entry (AGENTS.md R-4).

### Next-phase dependencies (Phase 2)

- The user explicitly authorises the start of Phase 2.
- Verify current stable versions of the ARCHITECTURE §13.1 Phase 2 dependencies against official registries.
- GitHub Actions must be enabled on the repository for the CI gates (AC-2.1, AC-2.2).
- Docker is available locally for the Compose Postgres (or a local PostgreSQL ≥ 16).

---

## Phase 2 — Application foundation, authentication and tenancy

- **Date:** 2026-10-16
- **Phase 1 bookkeeping carried forward:** the Phase 1 log-update commit is `d852b7cecc11dec031ce761f3cf5b3ced63ca099`. It was pushed to `origin/main` and verified with `git ls-remote` before this phase began.

### Objective and scope

Build the minimum reliable foundation: a monorepo (`apps/web`, `services/api`) with typed, fail-fast configuration, PostgreSQL via SQLAlchemy and Alembic, the minimum schema (users, sessions, businesses, memberships, dataset metadata, audit events), secure session authentication for a seeded demo account, business-scoped authorization with RLS, RFC 9457 errors, health and readiness probes, secure CORS, request correlation and structured logs, Docker for local PostgreSQL, an authenticated Next.js shell, and CI.

The scope follows the user's Phase 2 instruction. It absorbs most of the baseline Phase 3, recorded as **AMD-2**.

### Plan amendments made in this phase (ROOT_MASTER_PLAN §13)

| ID | Summary |
|---|---|
| AMD-1 | The layout is `services/api` and `apps/web` |
| AMD-2 | Phase 2/3 re-scoping. Self-registration, membership management and idempotency keys move to Phase 3 |
| AMD-3 | RLS is ENABLE (not FORCE). `/readyz` refuses a superuser, BYPASSRLS or table-owner runtime role |
| AMD-4 | Signed double-submit CSRF cookie. Leaner `users` and `sessions` columns. Lowercase-text email instead of citext |
| AMD-5 | Toolchain: Python 3.14, Node 24 LTS, TypeScript 5.9.3, ESLint 9, PostgreSQL 18. Coverage tooling. `urn:root:problem:*` type URIs. Next.js `cacheComponents` off. Minimal shadcn footprint |
| AMD-6 | `audit_log` rejects all UPDATE and DELETE until the Phase 12 retention design |
| AMD-7 | npm audit gate with one dated exception (GHSA-vfj7-8cjw-p6xm, dev-only, no fix exists) |

### Technical decisions and reasons

1. **Versions checked against PyPI and npm on 2026-10-16, then pinned exactly** (`uv.lock`, `package-lock.json`):
   - Backend: FastAPI 0.143.3, Pydantic 2.14.1, SQLAlchemy 2.1.4, psycopg 3.3.6, Alembic 1.20.0, argon2-cffi 25.1.0, uvicorn 0.54.0.
   - Frontend: Next.js 16.4.2, React 19.3.1, Tailwind 4.3.3, Vitest 5.0.5, Playwright 1.64.1.
   - **Node 24 LTS was installed with nvm** (Node 20 is end-of-life, and Vitest 5 needs ≥ 22). The machine's default Node 20 was left unchanged.
2. **Server-side opaque sessions** (ADR-010):
   - The token is `secrets.token_urlsafe(32)`. Only its SHA-256 is stored.
   - The cookie is HttpOnly and SameSite=Lax, and in production also Secure with the `__Host-` prefix.
   - Idle timeout 8 h, absolute timeout 7 days, server-side revocation.
   - The id rotates on login, and the previous session is revoked.
3. **CSRF:** a signed double-submit token bound to the session (AMD-4), plus an `Origin`/`Referer` check, enforced by a global ASGI middleware so no route can forget it.
4. **Throttling:**
   - 5 failures per account and 20 per IP within 15 min, counted from `auth_attempts`.
   - Email and IP are stored only as HMAC-SHA256 values.
   - Unknown users are verified against a dummy Argon2 hash, so timing and messages are uniform.
5. **Authorization:** `RequirePermission(permission)` is a class-based FastAPI dependency. It checks membership, then the permission, then sets the transaction-local RLS context with `set_config(..., true)`, which is pool-safe. Non-members get **404**, members without the permission get **403**, and both are audited as `authz.denied`.
6. **Defence in depth:**
   - RLS policies on `businesses`, `memberships`, `datasets` and `audit_log`.
   - A runtime role `root_app` (NOSUPERUSER NOBYPASSRLS, no DDL) with column-level grants. It can update only `users.password_hash` and `sessions.last_seen_at/revoked_at`.
   - Migrations and seeding use the owner role only.
7. **Unhandled exceptions** are caught in the request-context middleware, not by Starlette's outermost handler. 500 responses therefore still carry `X-Request-ID` and the security headers. The body is a generic problem.
8. **Body limit:** a 1 MB JSON cap enforced from both `Content-Length` and the streamed byte count. The internal exception derives from `BaseException`, because FastAPI's body parser converts any `Exception` into a 400 (found by test, see Errors).
9. **Same-origin API:** the Next.js rewrite `/api/v1/* → BACKEND_URL`. The browser never talks to the backend directly, and `BACKEND_URL` is server-only and validated at startup.
10. **Frontend CSP:** a per-request nonce in `src/proxy.ts`, the Next 16 name for middleware, implemented per the CSP guide bundled with Next 16.4. All app pages render dynamically.
11. **Demo account:** seeded by a CLI (`python -m app.cli seed-demo`) that reads credentials from the environment only. It validates the password policy, IANA time zone and ISO currency, and is idempotent. There is no public registration endpoint (AMD-2).
12. **No speculative code:**
    - Permissions are defined only for existing routes.
    - Unused shadcn packages were removed: `lucide-react`, `shadcn` and `tw-animate-css`.
    - A Google-hosted font was replaced by the system font stack, so the build makes no third-party fetch.

### Files created / changed / removed

| Area | Files |
|---|---|
| Repo root | `.gitignore`, `.env.example`, `README.md`, `docker-compose.yml`, `.github/workflows/ci.yml`, `infra/postgres/create-app-role.sh` |
| Backend config | `services/api/pyproject.toml`, `uv.lock`, `.python-version`, `.env.example`, `alembic.ini`, `openapi.json` (generated contract), `scripts/export_openapi.py` |
| Backend app | `app/main.py`, `app/models.py`, `app/health.py`, `app/cli.py`, `app/core/{config,db,errors,logging,middleware,pagination}.py`, `app/auth/{csrf,dependencies,passwords,router,schemas,service}.py`, `app/tenancy/{dependencies,permissions,router}.py`, `app/datasets/router.py`, `app/audit/{router,writer}.py` |
| Migrations | `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/0001_identity_tenancy_audit.py` |
| Backend tests | `tests/{conftest,helpers}.py`, `tests/unit/{test_config,test_security_primitives}.py`, `tests/integration/{test_auth,test_business_and_datasets,test_cli,test_health_and_migrations}.py`, `tests/security/{test_authorization,test_database_controls,test_http_controls,test_secret_exposure}.py` |
| Frontend config | `apps/web/{package.json,package-lock.json,.nvmrc,.env.example,next.config.ts,tsconfig.json,eslint.config.mjs,vitest.config.mts,playwright.config.ts,components.json}`, `scripts/audit.mjs` |
| Frontend app | `src/proxy.ts`, `src/app/{layout.tsx,globals.css}`, `src/app/login/{page,login-form}.tsx`, `src/app/(app)/{layout,page}.tsx`, `src/app/(app)/businesses/[businessId]/page.tsx`, `src/components/logout-button.tsx`, `src/components/ui/{alert,button,card,input,label}.tsx` (shadcn), `src/lib/session-cookie.ts`, `src/lib/api/{client,server,types}.ts`, `src/lib/api/schema.d.ts` (generated) |
| Frontend tests | `tests/client.test.ts`, `tests/login-form.test.tsx`, `e2e/auth-shell.spec.ts` |
| Docs | `docs/ROOT_MASTER_PLAN.md` (§13 AMD-1 to AMD-7 appended), this log section |

**Removed from the scaffold:** `apps/web/.gitignore` (replaced by the root one), `README.md`, the default `page.tsx`, `src/lib/utils.ts` and the Geist font.

### Code flow and execution flow

**Request path (backend).** The middleware runs outermost first:

1. SecurityHeaders
2. RequestContext: correlation ID, access log, 500 safety net
3. BodySizeLimit
4. CORS
5. CSRF: Origin/Referer check and token verification on unsafe `/api/` methods
6. Router: Pydantic strict validation, then `get_current_user` (session → user, idle refresh at most once a minute), then `RequirePermission` (membership → permission → RLS context), then the handler, then explicit commit of writes and audit rows in one transaction
7. Errors: RFC 9457 `application/problem+json`

**Authentication flow:**

1. `GET /api/v1/auth/csrf` sets the nonce cookie and returns the token (anonymous binding).
2. `POST /auth/login` (with token and Origin) runs: throttle check → Argon2id verify (dummy hash for unknown users) → `auth_attempts` row → on success, revoke any prior session cookie, create the session, write the audit row, commit, and set the HttpOnly cookie.
3. The client refetches the CSRF token, because the session binding changed.
4. `POST /auth/logout` revokes the session server-side and clears the cookie.

**Frontend:**

- `proxy.ts` sets a CSP nonce and redirects visitors without a session cookie to `/login`. This is optimistic only.
- Server components call `getMe()`, `getBusiness()` and `listDatasets()` against `BACKEND_URL`, forwarding the visitor's cookies. A 401 redirects to `/login` and a 404 calls `notFound()`.
- Client components post through `/api/v1` with the CSRF token, refreshing it once on `csrf_failed`.

### API and database changes

**API:**

- `GET /api/v1/healthz`, `GET /api/v1/readyz`
- `GET /auth/csrf`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`
- `GET /businesses/{business_id}` (`business:read`)
- `GET /businesses/{business_id}/datasets` and `/{dataset_id}` (`dataset:read`, cursor pagination)
- `GET /businesses/{business_id}/audit-log` (`audit:read`, owner only)
- OpenAPI and docs are exposed only outside production.

The dataset endpoints are complete read implementations. They return metadata once Phase 3 ingestion creates datasets.

**Migration `0001`:**

- Tables `users`, `sessions`, `auth_attempts`, `businesses`, `memberships`, `datasets`, `audit_log`, with CHECK constraints, unique `(business_id, version)` and keyset indexes.
- RLS policies.
- An append-only trigger on `audit_log`.
- Least-privilege grants to `root_app`.
- A pre-check that the role exists.
- The downgrade drops everything in dependency order, including the policy that references `memberships`.

### Security controls (with test evidence)

| Control | Requirement | Test |
|---|---|---|
| Argon2id, policy 12–128, rehash on parameter change | SEC-01 | `test_security_primitives`, `test_password_is_rehashed_when_parameters_change` |
| Session cookie attributes, rotation, idle and absolute expiry, server revocation, forged cookie rejected | SEC-02 | `test_auth.py` (7 tests) |
| CSRF: missing, wrong, cross-session and stale-after-login tokens; foreign, missing or lookalike Origin and Referer | SEC-03 | `test_http_controls.py` (11 tests) |
| Throttling per account and per IP, window expiry, uniform 401 body | SEC-04 | `test_auth.py` (4 tests) |
| Route inventory: every route authenticated or allowlisted, and every business route declares a permission. Role × route matrix | SEC-05 | `test_authorization.py` |
| Cross-tenant 404 with no data, foreign dataset via own business 404, lists scoped | SEC-06 | `test_authorization.py` |
| RLS: no context → 0 rows; reset context → 0 rows; scoped context → own rows; forged audit insert rejected; 8 forbidden statements denied | SEC-06 | `test_database_controls.py` |
| Strict input models, 422 without echoing input, 413 by length and by stream | SEC-07 | `test_auth.py`, `test_http_controls.py` |
| Security headers, HSTS in production only, docs hidden in production, nonce CSP on pages, no `X-Powered-By` | SEC-09/10 | `test_http_controls.py`, `e2e/auth-shell.spec.ts` |
| Secrets never in logs, responses or audit rows. Session token stored hashed only | SEC-11/24 | `test_secret_exposure.py`, `test_json_formatter_redacts_*` |
| Audit coverage for auth and denials, append-only even for the owner | SEC-18 | `test_auth_events_are_audited`, `test_database_controls.py` |
| Generic 500 and problem+json for 404/405/422 | SEC-19 | `test_http_controls.py` |
| CORS limited to `FRONTEND_ORIGIN` | SEC-25 | `test_cors_allows_only_frontend_origin` |
| Unsafe runtime DB role refused by readiness | AMD-3 | `test_readyz_503_when_connected_as_owner_role` |

### Performance considerations

- Async SQLAlchemy with a bounded pool (`DB_POOL_SIZE`, default 5, plus equal overflow, `pool_pre_ping`, 5 s connect timeout).
- Session idle refresh writes at most once a minute per session.
- Throttling queries use `(email_hash, at)` and `(ip_hash, at)` indexes with a `LIMIT`ed subquery.
- Keyset pagination: datasets by `(business_id, version)` unique index, audit by `(business_id, id DESC)` using time-ordered UUIDv7.
- Uvicorn's duplicate access log is disabled.
- No load benchmarks were run in this phase. NFR-02 benchmarks belong to ingestion (Phase 3) and Phase 12.

### Tests executed and actual results (all run in this session)

Environment: macOS, Python 3.14.8, Node 24.22.0, and a **disposable PostgreSQL 18.6 cluster** (`initdb` in the session scratchpad, TCP 55432, SCRAM auth, `root_app` created by `infra/postgres/create-app-role.sh`). Each pytest session creates and drops its own database.

| Check | Command | Result |
|---|---|---|
| Backend install | `uv sync --locked` | OK |
| Lint and format | `uv run ruff check .` / `uv run ruff format --check .` | All checks passed. 49 files formatted |
| Types | `uv run mypy` (strict, app + migrations + scripts + tests) | No issues in 49 source files |
| Backend tests | `uv run pytest --cov --cov-fail-under=85` | **122 passed** (unit 27, integration 45, security 50). Coverage **96.85%** |
| Auth and tenancy coverage | `coverage report --include='app/auth/*,app/tenancy/*' --fail-under=95` | **99%** |
| Migrations up, down, up | `test_migrations_upgrade_downgrade_upgrade` (fresh disposable DB), plus a manual `alembic upgrade/downgrade/upgrade` on the dev DB | Passed. After downgrade only `alembic_version` remains |
| Python dependency audit | `uv run pip-audit` | No known vulnerabilities found |
| OpenAPI drift | `python -m scripts.export_openapi && git diff --exit-code openapi.json` | No drift |
| Frontend install | `npm install` (exact pins) | OK |
| Frontend lint | `npm run lint` (`--max-warnings=0`) | OK |
| Frontend types | `npm run typecheck` (`next typegen && tsc --noEmit`) | Exit 0 |
| Frontend unit tests | `npm run test:coverage` | **18 passed**. Statements 98.63%, branches 87.71% |
| Generated API types drift | `npm run api:types && git diff --exit-code src/lib/api/schema.d.ts` | No drift |
| Production build | `npm run build` | Exit 0. `/`, `/login` and `/businesses/[businessId]` are dynamic |
| npm audit (production deps) | `npm audit --omit=dev --audit-level=high` | 0 vulnerabilities |
| npm audit gate | `npm run audit:gate` | Passed (only AMD-7 exception). A negative test with the exception removed failed as expected (exit 1) |
| Browser smoke test | `npx playwright test` (Chromium, production build + real API + PostgreSQL + seeded demo account) | **4 passed**: redirect when signed out, nonce CSP and headers, wrong-password error, then sign in → business shell → HttpOnly cookie not readable by JS → cross-tenant URL shows not-found → sign out. No console or CSP errors |
| Secret scan | gitleaks 8.30.1 (checksum-verified) `gitleaks git --staged` | No leaks. A sentinel scan confirmed no local scratch secrets in staged files |
| Docker Compose | `docker compose up -d --wait` (PostgreSQL 18.6, digest-pinned), then `alembic upgrade head`, then the API on `root_app` and `GET /readyz`, then `alembic downgrade base`, then `docker compose down -v` | Healthy. `root_app` super=false, bypassrls=false. Migration applied. `/readyz` → `{"status":"ready"}`. Clean teardown |

**Docker note:** Docker Desktop was not running at the start of the phase. It was started only for this verification and quit afterwards. While it ran, the user's unrelated containers with restart policies resumed. They were not modified. The pinned `postgres:18.6-trixie` image remains in the local Docker cache.

### Errors encountered and fixes applied

1. **Migration downgrade failed** (`DependentObjectsStillExist`): the `businesses` RLS policy references `memberships`. Fixed by dropping the policy before the table.
2. **Leading or trailing spaces in the login email** were rejected by the pattern before normalisation. Fixed with `StringConstraints(strip_whitespace=True)`.
3. **Async engines were never disposed in tests** (`ResourceWarning`), because `ASGITransport` doesn't run the lifespan. Fixtures now enter `app.router.lifespan_context`.
4. **The route-inventory test was vacuous at first:** FastAPI 0.143 wraps included routers in `_IncludedRouter`. The test now uses the public `fastapi.routing.iter_route_contexts` and asserts that routes were discovered.
5. **A streamed oversized body returned 400, not 413:** FastAPI's body parser converts `Exception` into 400. The limit exception now derives from `BaseException`, with a comment.
6. **App loggers went silent after in-process migrations:** Alembic's `fileConfig` disabled existing loggers. Fixed with `disable_existing_loggers=False`. The secret-exposure test caught this.
7. **Logout button bug (found by Vitest):** `try/finally` re-threw a failed logout as an unhandled rejection and navigated as if sign-out had succeeded. It now shows an alert and stays.
8. **Playwright alert locator** was ambiguous with Next's route announcer (`role=alert`). The locator now filters by text.
9. **`create-next-app` defaults** (`cacheComponents`, `@tailwindcss/turbopack`, TypeScript ^5) were reviewed. `cacheComponents` was disabled (AMD-5). TypeScript 6 conflicted with `openapi-typescript`'s peer range, so TypeScript 5.9.3 is used.
10. **npm audit:** 9, then 5, high findings from one advisory with no fix. The shadcn CLI chain was removed and AMD-7 documents the exception for the remaining dev-only chain.
11. **Local port 8000 was already in use by an unrelated process.** It was left untouched. Playwright derives the API port from `BACKEND_URL`, and local runs used 8010.
12. **Compose init bug (found by real Docker verification):** the image's entrypoint *executes* `create-app-role.sh`, so `psql` defaulted to the OS user `postgres` and failed (`role "postgres" does not exist`, exit 2). The script now defaults `PGUSER`/`PGDATABASE` to `POSTGRES_USER`/`POSTGRES_DB`. It was re-verified in Docker and against the scratch cluster.
13. Lint findings (ruff `S101`, `PT012`, `PT018`, `E501`) and mypy findings (`no-any-return`, Literal role, unused ignores) were fixed without disabling rules.

### Known limitations and remaining risks

| Risk | Note |
|---|---|
| CI has not run yet | The workflow was validated as YAML and every step was executed locally. The first GitHub Actions run happens on push. Its result is recorded below if observable |
| Client IP behind proxies | Per-IP throttling uses `request.client`. In production, uvicorn must run with `--forwarded-allow-ips` set to the platform proxy only. Trusting `*` would let clients spoof X-Forwarded-For and evade the per-IP limit (the per-account limit still applies). To be configured and tested in Phase 13 |
| E2E uses a real failed login | Repeated local runs within 15 min, 5 or more times, trigger the account throttle by design. CI seeds a fresh database each run |
| The `/_not-found` page is static | It is served with the nonce CSP but carries no nonce on its scripts. The HTML renders, but hydration scripts are blocked on unknown URLs. Acceptable. Revisit if interactive 404s are needed |
| `style-src 'unsafe-inline'` | Residual risk as documented in SECURITY_MODEL §7 |
| Dev-only `braces` advisory | AMD-7, expires 2027-01-31 |
| Throttle state grows | `auth_attempts` and expired `sessions` are purged by the retention sweep (Phase 12) |

### Acceptance criteria status

| AC | Status | Evidence |
|---|---|---|
| AC-2.1 Backend gates pass (local + CI) | Met locally. CI: see push section | test table |
| AC-2.2 Frontend gates pass (local + CI) | Met locally. CI: see push section | test table |
| AC-2.3 `/readyz` 503 when DB unreachable, 200 when reachable | Met | `test_readyz_*` |
| AC-2.4 Unknown routes return problem+json without stack traces | Met | `test_unknown_route_is_problem_json`, `test_unhandled_error_is_generic_500` |
| AC-2.5 Dependency and secret scans clean | Met (pip-audit 0, npm prod 0, npm gate with AMD-7, gitleaks 0) | test table |
| AC-2.6 No unused dependencies | Met. Every runtime dependency is imported or invoked (uvicorn CLI, psycopg driver) | review |
| AC-3.1 to AC-3.6 (moved by AMD-2) | Met for all existing routes | security test table |
| User gate: reproducible start, connects to PostgreSQL, secure auth, tenant isolation, all tests pass | Met (Compose verification below) | above |

---

## Phase 3 — Data ingestion and validation

- **Date:** 2026-10-16
- **Numbering:** user Phase 3 is the baseline Phase 4 scope plus the user's additions (AMD-8).

### Phase 2 bookkeeping carried forward

- Phase 2 commit `0881d4bb9682b63d87e978203611b02182a2881b` was pushed to `origin/main` and verified with `git ls-remote` (local = remote).
- **GitHub Actions result for `0881d4b`: not observed.** The repository is private, the GitHub CLI is not installed, and no API credentials were used. Reading the keychain git credential was deliberately avoided. AC-2.1 and AC-2.2 are therefore met **locally only**, and their CI half remains **unverified** until the repository owner checks the Actions tab.

### Objective and scope

Build secure, efficient, reproducible ingestion of four related datasets (`orders.csv`, `products.csv`, `customers.csv`, `returns.csv`):

- business-scoped uploads with strict file checks
- canonical typed tables with primary and foreign keys
- column-mapping proposals with a human confirmation checkpoint
- row and set-based validation with explained rejections
- batched, transactional persistence
- explicit dataset versioning with duplicate and idempotency semantics
- currency and time interpretation, profiling and provenance
- record retrieval, formula-safe CSV export, and an upload UI with a data-quality report

AI-generated conclusions were explicitly out of scope, and no LLM or agent code was written.

### Technical decisions and reasons

1. **Synchronous request checks, asynchronous ingestion.** The request does only byte-level work (one chunked pass: size, SHA-256, strict incremental UTF-8, NUL and binary signatures, header, mapping), then stores the files and queues a job. Heavy parsing runs in the Postgres-backed job queue (ADR-003, implemented now), so HTTP latency stays flat (0.16 s for 20k lines).
2. **One transaction per ingestion, staging through `COPY`.** Rows stream into `ON COMMIT DROP` temp tables. Cross-row rules run as SQL (duplicates, references, cumulative returns with window functions), then `INSERT … SELECT` loads the canonical tables. Any failure rolls back everything; there is never a partially loaded dataset.
3. **RLS kept intact.** PostgreSQL forbids `COPY FROM` into RLS tables, so issues also go through a temp table. The app role still never gets BYPASSRLS (AMD-9).
4. **Mapping never guesses.** Exact header matches are automatic. Synonym and ambiguous matches block ingestion until a human confirms each one (`awaiting_mapping`). Missing required columns are rejected immediately, and the accepted header names are listed.
5. **PII never stored.** Personal-data columns are stripped from the stored bytes at upload. A row whose field count is wrong can't be split safely, so it becomes an empty line and is reported as V-010.
6. **Strict, explained value parsing.** ISO-8601 only (no day/month guessing), DST gaps rejected, DST overlaps flagged, exact decimals, bounded quantities. Every rejection carries a code, a message and a sanitised excerpt.
7. **Versioning semantics.**
   - A per-business advisory lock serialises idempotency replay, duplicate detection and version allocation. This makes concurrent uploads safe (tested).
   - Identical content is refused with 409, naming the version. A corrected re-upload creates version N+1.
   - Terminal versions are immutable (trigger plus grants).
8. **Time.** Each dataset snapshots `source_timezone` and the business time zone at upload, so later business-setting changes cannot reinterpret stored data.
9. **Bounded resources.**
   - Limits: 25 MB per file, 60 MB per request (enforced by streaming), row limits per file, 10,000-character field limit, 100 columns.
   - Issue rows stored are capped per code (exact counts are kept).
   - Exports are built in a spooled temp file.
   - CSVs are stream-decoded: an earlier version decoded whole files first. Measured in the benchmark, the change cut peak Python memory about 5×.
10. **Authorization before parsing.** The upload handler reads the multipart body only after `RequirePermission`, so unauthenticated or unauthorized clients cannot make the server spool files.

### Files created / changed

| Area | Files |
|---|---|
| Migration | `services/api/migrations/versions/0002_ingestion.py` (new tables, statuses, triggers, RLS, grants, reversible downgrade) |
| Ingestion | `app/ingestion/{contract,mapping,parsing,upload,pipeline,export}.py` |
| Jobs | `app/jobs/{queue,worker}.py`, worker started in `app/main.py` lifespan |
| API | `app/datasets/{router,service}.py` (upload, mapping, detail, issues, records, export). `app/tenancy/router.py` (`PATCH` business). `app/tenancy/permissions.py` (`dataset:upload`, `business:update`). `app/core/{errors,middleware,config,db}.py` (error lists, per-route body limit, worker settings, business RLS context). `app/models.py` |
| Tooling | `scripts/generate_dataset.py` (seeded generator, FR-29), `scripts/__init__.py`, `openapi.json` regenerated, `pyproject.toml` / `uv.lock` (`python-multipart`, perf marker, scoped lint exceptions) |
| Tests | `tests/integration/{test_ingestion,test_upload_guards}.py`, `tests/security/test_ingestion_isolation.py`, `tests/unit/test_ingestion_units.py`, `tests/perf/test_ingestion_benchmark.py`, `tests/ingestion_fixtures.py`. Updated: `conftest.py`, `helpers.py`, authorization matrix (now covers unsafe methods), database controls, migration test |
| Frontend | `src/app/(app)/businesses/[businessId]/datasets/new/{page,upload-form}.tsx`, `…/datasets/[datasetId]/{page,mapping-review}.tsx`, `src/components/auto-refresh.tsx`, business page links, `src/lib/api/{client,server,types}.ts` (multipart client with idempotency key, problem details), `tests/datasets.test.tsx`, `e2e/datasets.spec.ts` |
| CI and docs | `.github/workflows/ci.yml` (on-demand benchmark job), `README.md`, `docs/ROOT_MASTER_PLAN.md` (AMD-8, AMD-9), `docs/DATA_CONTRACTS.md` (§10 as-built addendum, plus "Amended by" notes in §2, §2.6, §6.2, §7.4) |

### Upload pipeline and execution flow

```
POST /businesses/{bid}/datasets (CSRF, Idempotency-Key, multipart)
  → RequirePermission(dataset:upload) → parse form (≤4 files, ≤2 fields, spooled temp files)
  → per file in a thread: extension/content-type → chunked SHA-256 + strict UTF-8 + NUL/signature
    → header → normalise → propose mapping (exact/proposed/ambiguous) → strip PII columns
  → timezone/currency checks → advisory lock(business) → idempotency replay? → quota
    → duplicate checksum? → version N+1 → datasets + dataset_files (+ job if no decision needed)
    → audit → store idempotent response → COMMIT → 202
[awaiting_mapping] POST …/mapping → validate decisions → status queued + job → 202
worker: lease job → tx1: status validating → tx2: temp staging tables → stream-parse + COPY
  → SQL rules → thresholds → INSERT…SELECT canonical tables → profiles/stats → ready|rejected
  (exception → rollback → retry if transient → final failure marks dataset failed)
GET …/datasets/{id} (polled by the UI) · …/issues · …/records/{kind} · …/export/{kind}
```

### Database changes (migration `0002`)

The full schema is in DATA_CONTRACTS §10.6:

- new tables `dataset_files`, `products`, `customers`, `order_lines`, `returns`, `validation_issues`, `idempotency_keys`, `jobs`
- new dataset statuses and columns, with `sales_rows` renamed to `orders_rows`
- an immutability trigger
- composite primary and foreign keys, and CHECK constraints mirroring the value rules
- RLS on every tenant table, plus column-level grants

The downgrade to `0001` and the upgrade back are tested.

### Security controls (with tests)

| Control | Tests |
|---|---|
| Authorization before body parsing. Role matrix and cross-tenant 404 now enumerate **all** methods, including the upload, mapping and `PATCH` routes. Cross-tenant attempts change nothing (verified in the database) | `security/test_authorization.py` |
| RLS on the record tables: no context, a foreign context and a forged insert all fail | `security/test_ingestion_isolation.py`, `security/test_database_controls.py` |
| Malicious or malformed files: empty, wrong type, PDF/ZIP/XLSX, UTF-16, Latin-1, NUL, unterminated quote, >100 columns, duplicate headers, oversize per file and per request | `integration/test_upload_guards.py` (14-case matrix and others) |
| No PII stored: raw bytes, headers, issue excerpts and records are searched for the PII sentinel values | `test_ambiguous_mapping_requires_human_confirmation`, `test_pii_column_in_customers_is_dropped` |
| Formula injection: exports escape `= + - @ \t \r`, checked on every exported cell | `test_safe_export_escapes_formulas`, `unit/test_safe_cell`, e2e export check |
| Sanitised errors: no internals in failure states (`unexpected_error` code only) | `test_failure_rolls_back_every_write` |
| Upload quota (SEC-20), idempotency (SEC-14) | `test_upload_quota`, `test_idempotent_retries_and_duplicate_content` |
| Untrusted uploaded text is never passed to an LLM or agent instruction (no LLM exists yet). `reason_text` is stored as data only | design. To be re-verified by T-22 in the LLM phase |

### Validation logic

Rule semantics are in DATA_CONTRACTS §10.4. **Every issue code in `ISSUE_CODES` is referenced by at least one test.** This was checked with a script, and three codes (V-015, V-016, V-043) were missing a fixture until it ran.

### Performance (measured, not estimated)

Environment: macOS, Intel Core i9-8950HK (6 cores / 12 threads), 32 GB RAM, local PostgreSQL 18.6, Python 3.14.8. The data comes from the seeded generator (26 weeks, 3,000 customers, 150 products, about 5% returns). The command is `uv run pytest tests/perf -m perf -s`.

Final code (stream-decoding):

| Order lines | Upload size | Upload request | Ingestion job | Total | Lines/s | Python peak (tracemalloc, separate run) | Process peak RSS before → after |
|---|---|---|---|---|---|---|---|
| 20,326 | 1.57 MB | 0.163 s | 2.461 s | **2.62 s** | 8,261 | 1.7 MB | 178 → 201 MB |
| 100,961 | 7.41 MB | 0.332 s | 13.190 s | **13.52 s** | 7,654 | 7.6 MB | 219 → 310 MB |

Earlier run, before switching to stream-decoding (same seeds): 20,326 lines in 2.09 s total with a 8.7 MB Python peak; 100,961 lines in 9.47 s with a 43.2 MB Python peak.

The process-RSS figures cover the whole pytest process (test client, driver buffers, previous allocations) and are high-water marks, so they are upper bounds rather than ingestion-only costs. NFR-02 (100k lines ingest and validate ≤ 30 s) **is met on this hardware**. It has not been measured on CI or production hardware yet; the benchmark job is available through `workflow_dispatch`.

Query design avoids N+1: per-row work happens in Python streaming and `COPY`, and every cross-row rule is one set-based statement per rule. The staging tables are `ANALYZE`d before the rules run, and the canonical tables are indexed on `(business_id, dataset_id, date)` and the foreign-key columns.

### Tests executed and actual results

| Check | Command | Result |
|---|---|---|
| Backend lint and format | `uv run ruff check .` / `ruff format --check .` | All checks passed. 69 files formatted |
| Backend types | `uv run mypy` (strict, 69 files) | No issues |
| Backend tests | `uv run pytest --cov --cov-fail-under=85` | **247 passed** (unit 94, integration 98, security 55), 2 perf deselected. Coverage **96.16%** |
| Auth and tenancy coverage gate | `coverage report --include='app/auth/*,app/tenancy/*' --fail-under=95` | Passed |
| Benchmarks | `uv run pytest tests/perf -m perf -s` | 2 passed (table above) |
| Python audit | `uv run pip-audit` | No known vulnerabilities |
| OpenAPI and TypeScript contract | `export_openapi` then `api:types` | Regenerated. No drift between them |
| Frontend | `npm ci`, `lint`, `typecheck`, `test:coverage`, `build` | All exit 0. **28 Vitest tests** passed. Statements 98.6%, branches 88.23% |
| npm audit | `npm audit --omit=dev --audit-level=high` / `npm run audit:gate` | 0 production findings. Gate passed (only the AMD-7 exception) |
| Browser end-to-end | `npx playwright test` (production build + real API + worker + PostgreSQL) | **6 passed**: the 4 Phase 2 smoke tests, plus (1) upload, then live status, then a rejected dataset with V-013 and V-041 shown in the report and the issues CSV downloaded; (2) a synonym header held for confirmation, then confirmed, then `ready`, then a product export containing `'=SUM(A1)` |
| Database migrations | `test_migrations_upgrade_downgrade_upgrade` (base ↔ 0001 ↔ 0002) and `alembic upgrade head` on the dev database | Passed |

**User-required test checklist:**

| Test | Where it is covered |
|---|---|
| Valid complete upload | `test_valid_dataset_is_persisted_exactly`, e2e |
| Empty CSV, incorrect type | upload-guard matrix |
| Unexpected encoding, corrupt files | upload-guard matrix, `test_structurally_broken_csv_inside_file_is_rejected` |
| Oversized upload | `test_oversized_files_are_rejected` |
| Duplicate keys | `test_duplicate_keys` |
| Missing required columns | matrix, `test_missing_required_columns_explain_accepted_headers` |
| Optional columns | `test_optional_columns_may_be_absent` |
| Ambiguous mapping | `test_ambiguous_mapping_requires_human_confirmation`, e2e |
| Missing parents | `test_relational_rules_reject_rows` (V-030, V-031, V-032), `test_cascade_rejection_follows_references` |
| Invalid currency | `test_currency_values` |
| Invalid dates and time zones | `test_value_validation_and_time_interpretation`, `test_invalid_source_timezone_is_rejected`, unit date tests |
| Returns exceeding purchase | V-035 and V-036 cases |
| Cross-tenant access | authorization matrix, isolation test |
| Duplicate uploads | `test_idempotent_retries_and_duplicate_content` |
| Transaction rollback | `test_failure_rolls_back_every_write` |
| Concurrent uploads | `test_concurrent_uploads_get_distinct_versions` |
| Large-file performance | perf tests |
| Corrected re-upload | `test_reupload_of_corrected_dataset_creates_new_version` |
| Safe export | `test_safe_export_escapes_formulas` |

Stored records are compared field by field against the source rows, and referential integrity is verified with orphan queries that must return 0.

### Errors encountered and fixes applied

1. `python-multipart` was missing, so form parsing failed. It was added as a pinned dependency (AMD-9).
2. **`COPY FROM not supported with row-level security`** on `validation_issues`. Fixed by copying into a temp table, then `INSERT … SELECT` (AMD-9).
3. **Mapping confirmation lost the PII record.** Confirmation recomputed `pii_dropped` from headers that had already been stripped, which dropped V-041. It now carries the original list forward.
4. **Too many multipart parts returned 400 instead of 422.** Starlette raises `HTTPException(400)`, not `MultiPartException`. Both are now mapped to a 422 problem.
5. **SQL predicate bug, found in review before any test ran:** the helper built `o.NOT o.rejected`. Fixed with an explicit `ACCEPTED_O` predicate.
6. **Memory:** whole-file decoding was replaced with stream-decoding. Measured effect: 5.1× (20k lines) and 5.7× (100k lines) lower Python peak.
7. Test-side corrections, not product defects:
   - A future-dated DST sample was correctly rejected, so the sample moved to 2025.
   - The 5% threshold needed padding rows in the single-error fixtures.
   - The mapping unit test's selection was incomplete; the product correctly demands every decision.
8. Lint and typing:
   - Dynamic staging SQL got a scoped, documented per-file S608 exception: identifiers come from module constants and all values are bound.
   - The generator got a scoped S311 exception.
   - Exception classes were renamed (N818).
   - Mypy variance fixes in the generator.

### Known limitations and remaining risks

| Risk | Note / owner phase |
|---|---|
| CI unobserved for Phase 2 and Phase 3 commits | Private repo, no API access from this session. The owner should check the Actions tab |
| Platform body limits | 60 MB uploads go through the Next.js rewrite. Locally this is unbuffered (the proxy matcher excludes `/api`). Vercel's limits on externally rewritten requests must be verified in the deployment phase |
| The worker shares the API process | Ingestion CPU (about 8k lines/s) competes with requests. Fine at the target scale; to be measured under load in hardening |
| Raw files kept in the database for 30 days | Purge job, plus dataset deletion, come in hardening (AMD-8) |
| Cumulative-return semantics | They count all earlier valid returns, documented in §10.4. A return rejected for other reasons does not count |
| Issue storage cap (10,000 per code) | Counts stay exact. The report states the cap |
| E2E test data accumulates | Each local e2e run uploads two datasets to the seeded business. The upload quota (10/hour) can be reached with more than 5 runs per hour by design. CI uses a fresh database |
| No LLM yet | The "untrusted content never reaches agent instructions" rule must be enforced and tested (T-22) when the LLM gateway arrives |

### Acceptance criteria status (baseline AC-4.x, plus the user's completion gate)

| AC | Status | Evidence |
|---|---|---|
| AC-4.1 Every rule has a failing-fixture test with its code | Met | Script check: 0 codes without a test |
| AC-4.2 Oversize rejected before full buffering | Met. `Content-Length` precheck and streamed byte counting at 60 MB; files over 1 MB spool to disk, not memory | `test_oversized_files_are_rejected`, middleware tests |
| AC-4.3 PII never reaches the database | Met | PII sentinel searches across raw files, headers, issues and records |
| AC-4.4 Ready dataset immutable (API and database) | Met | `test_ready_dataset_is_immutable`, `test_reupload_…` |
| AC-4.5 100k lines in ≤ 30 s | Met on local hardware (13.52 s). CI and production not yet measured | benchmark |
| AC-4.6 Same seed gives byte-identical CSVs | Met | `test_generator_is_deterministic` |
| User gate: all four datasets uploaded, validated, normalised, persisted and retrieved without silent corruption | Met | field-by-field comparison, orphan queries, records API, e2e |

### Commit hash and GitHub push status

The hash is recorded at the start of the next phase entry, as for previous phases. It is also reported in the session summary.

### Next-phase dependencies

- Phase 4 (baseline Phase 5, analytics) reads `order_lines`, `returns`, `products` and `customers` for `ready` datasets only, using `order_date` and `return_date` (business-local) and NUMERIC money.
- The owner should confirm the CI results for `0881d4b` and the Phase 3 commit.

---

## Phase 4 — Analytics engine, segmentation and deterministic anomaly detection

- **Date:** 2026-10-16
- **Phase 3 bookkeeping:**
  - Commit `4bb72ba36ed34eb75a9b3b3d0f7d2e4ff88ec2d3` was pushed to `origin/main` and verified with `git ls-remote` (local = remote).
  - GitHub Actions results for `0881d4b` and `4bb72ba` are **still unobserved**: the repository is private and this session has no API access. AC-2.1 and AC-2.2 remain met **locally only**.

### Objective and scope

Build an accurate, explainable analytics engine with no LLM involved in any number:
- revenue, discounts and refunds; recognised COGS estimates; gross profit and margin
- equal-period comparisons; category and product performance with return rates
- purchase frequency, recency and repeat-purchase analysis
- lifecycle segmentation: loyal, at_risk, dormant, insufficient_history
- historical baselines and deterministic anomaly detection, with severity and records in scope
- source-level evidence, and data-quality restrictions that block unsupported calculations

Scope mapping is AMD-10: baseline Phase 5 plus the deterministic parts of baseline Phase 6. The hypothesis catalog and verification stay with the LLM phase.

### Plan amendments

- **AMD-10** (recorded before code):
  - segment vocabulary
  - cancelled lines (optional `status` column, V-038)
  - optional costs (V-045) with restrictions
  - the equal-period definition
  - the restrictions taxonomy
  - detection details left open by ARCHITECTURE §5
  - severity, records in scope and evidence kinds
  - precision
  - no pandas; `hypothesis` as a dev dependency
  - the `analysis:run` permission
  - analysis as a job that runs automatically after ingestion
- **AMD-11** (recorded from findings): `aov` is an aggregate ratio; default API ranges end at the last activity; `refresh_dataset_statistics()`.

### Formulas and computational assumptions (as implemented)

- **Metrics:** M01–M21 exactly as DATA_CONTRACTS §3.3, implemented once in `app/analytics/metrics.py` (`derive(Totals)`):
  - `net_sales` = gross − discounts
  - `net_revenue` = net_sales − refunds (refunds dated by return_date)
  - `gross_profit` = net_revenue − cogs_sold + cogs_restocked, where restocked means reason ∉ {damaged, defective}
  - `gross_margin_pct` = gross_profit / net_revenue (null when net_revenue ≤ 0)
  - aov, discount, refund, unit-return and repeat-customer rates are ratios of sums (A11)
- **Cancelled orders:** excluded from L, reported separately. Returns of cancelled lines are rejected at ingestion (V-038).
- **Partial refunds and quantities:** refunds are summed as recorded (V-035/V-036 ensure they never exceed the purchase). Unit return rate uses units, refund rate uses money.
- **Restocked returns:** COGS is reversed only for restockable reasons (A5). Written-off returns reverse nothing.
- **Missing historical costs:**
  - `unit_cost` may be blank (V-045 warning).
  - COGS, gross profit and margin are `null` in any scope that touches a cost-less product, and a `missing_costs` restriction names the products.
  - Recognised COGS is an estimate under A4 (current cost applied to all periods), and is labelled as such.
- **Zero denominators:** `null`, never 0 (tested for every ratio).
- **Incomplete observation windows:**
  - Detection uses complete ISO weeks only and needs at least 8 baseline weeks.
  - Comparisons are `unavailable` when the earlier period starts before the data.
  - `incomplete_period` and `immature_returns` restrictions are reported.
  - M21 only uses lines at least 14 days old.
- **Currency precision:** `NUMERIC(18,4)` storage, SQL `NUMERIC` sums, Python `Decimal` in a 60-digit context. Output is 4-dp money and 6-dp ratios (half-even), always from unrounded sums. Values above 10¹⁴ are tested to be exact.
- **Churn claims:** none are made. `at_risk` means "overdue versus own cadence" and `dormant` means "no purchase for 180+ days or 4x the usual gap". Rules and reasons are exposed, and a test asserts that segment reasons never say "churn".

### Code flow

```
ingest_dataset (ready) ── same tx ──► refresh_dataset_statistics() + request_analysis() → job analyze_dataset
analyze_dataset: tx1 status running → tx2:
  queries.dataset_bounds → T, first order, cancelled counts
  queries.customer_stats(T) → segmentation.segment_customers (RFM + lifecycle + flags)
  queries.aggregate(overall weekly, whole range)  → metrics snapshot (hashed)
  periods.detection_windows → insufficient? → status insufficient_data (segments still stored)
  segments as of baseline end → temp table (COPY + ANALYZE)
  queries.aggregate(by week, GROUPING SETS: overall/category/product/region/channel/segment)
  detection.evaluate_slice per (applicable metric × slice) → Flag | SliceSkip | None
  queries.window_lines(W) → order sets, records in scope → detection.consolidate → incidents
  evidence: detection stats, source_records (source rows), return_reason_mix
  persist: customer_segments (COPY → INSERT…SELECT), incidents, evidence_items, hashes, revision
API reads: queries.aggregate (+ M21 query) → metrics.derive/present → restrictions → response
```

**Layering:**
- `queries.py`: raw data access, the only module with SQL.
- `metrics.py`: aggregation algebra and metric definitions.
- `segmentation.py`, `detection.py`, `evidence.py`, `periods.py`: pure functions with no I/O.
- `service.py`: orchestration and persistence.
- `router.py`: HTTP.

### API and database changes

- **Endpoints** (DATA_CONTRACTS §11.5): `/metrics`, `/metrics/comparison`, `/products`, `/segments`, `/customers`, `GET` and `POST /analysis`, `/incidents/{id}`.
- **Migration `0003`** (§11.6), including RLS on all new tenant tables, an immutability trigger on finished revisions, the v1 analysis config seeded and read-only, and the statistics function.

### Security controls

- Every new route has a declared permission. The role matrix and cross-tenant tests enumerate them automatically: 404 cross-tenant, 403 when the permission is missing, no 5xx.
- RLS applies to all analytics tables: no context, a foreign context and a forged insert all fail (tested).
- The app role cannot change incidents, evidence, segments or configs (privilege tests).
- SQL dimension expressions come only from an allowlist (`DIMENSION_SQL`). Unknown dimensions return 422 at the API and raise `ValueError` in code.
- `refresh_dataset_statistics()` is the only `SECURITY DEFINER` function: it takes no input, has a fixed `search_path` and returns no tenant data.
- Pseudonymous customer IDs only. The page shows evidence source rows by file and row number.

### Performance and benchmarks (measured)

Measured on macOS, i9-8950HK, local PostgreSQL 18.6, with seeded data (26 weeks, 3,000 customers, 150 products). Commands: `pytest tests/perf -m perf -s`.

**Analysis job and representative queries** (statement counts from SQLAlchemy cursor events; wall time includes the full API stack):

| Operation | ~20k lines (20,271) | ~100k lines (101,107) |
|---|---|---|
| Analysis job (segments ×2, weekly GROUPING SETS, 298–327 slice tests, evidence, persistence) | **25 statements, 0.73 s** | **25 statements, 2.12 s** |
| `GET /metrics` (total) | 12 statements, 0.095 s | 12, 0.354 s |
| `GET /metrics?granularity=week` | 12, 0.100 s | 12, 0.439 s |
| `GET /metrics?granularity=week&dimension=category` | 12, 0.121 s | 12, 0.551 s |
| `GET /metrics?dimension=customer_segment` | 17, 0.116 s | 17, 0.529 s |
| `GET /metrics/comparison?dimension=product` | 14, 0.135 s | 14, 0.472 s |
| `GET /products` | 14, 0.159 s | 14, 0.642 s |
| `GET /segments`, `GET /analysis` | 10, 0.013 s / 10, 0.011 s | 10, 0.013 s / 10, 0.011 s |

The statement counts include the per-request session, membership, RLS and dataset lookups. The analysis job count does not grow with data size.

**Ingestion** (generator changed in this phase, and ANALYZE is now included):

| Order lines | Upload request | Ingestion job | Total | Lines/s | Python peak |
|---|---|---|---|---|---|
| 20,271 | 0.143 s | 2.058 s | 2.20 s | 9,851 | 5.0 MB |
| 101,107 | 0.287 s | 10.431 s | 10.72 s | 9,693 | 6.9 MB |

**Two performance defects found and fixed by measurement:**
1. **Stale planner statistics after bulk ingestion.** From the third dataset in a database onward, analysis took 24–27 s instead of 0.9 s: nested loops rescanned returns for each order line. Fixed with `refresh_dataset_statistics()` (AMD-11) and with window-function first-order computation, which removes the aggregate join whose plan depended on statistics. After the fix: 0.8–0.9 s per dataset across six consecutive datasets.
2. **Segment persistence round trips.** SQLAlchemy's batched insert split into about 1,067 statements for 3,000 rows, because rows had varying shapes (optional gaps, flag arrays). Replaced by COPY into a temp table plus one `INSERT … SELECT`. The analysis job went from 1,090 to 25 statements.

### Tests executed and actual results

| Check | Result |
|---|---|
| `uv run ruff check .` / `ruff format --check .` | All checks passed. 85 files formatted (`allowed-confusables` added for × − – ≤ ≥ in formulas) |
| `uv run mypy` (strict, 85 files) | No issues. All new analytics code is free of type ignores, except the documented loosely-typed driver-connection one |
| `uv run pytest --cov` | **321 passed** (unit 135, integration 127, security 59), 4 perf deselected. Total coverage **96%**. **`app/analytics` 96%** (gate 95%). Auth and tenancy gate (≥ 95%) passed |
| `uv run pytest tests/perf -m perf -s` | 4 passed (tables above) |
| `uv run pip-audit` | No known vulnerabilities |
| Frontend `lint`, `typecheck`, `test:coverage`, `build` | All exit 0. **30 Vitest tests**. Statements 97.71%, branches 86.95% |
| `npm audit --omit=dev --audit-level=high` / `npm run audit:gate` | 0 / passed (AMD-7 exception only) |
| `npx playwright test` (production build + API + worker + PostgreSQL) | **7 passed**, including the new analytics page test: data limitations, product net revenue, segment counts |
| Migration up/down/up (base ↔ 0001 ↔ 0002 ↔ 0003) | Passed |

**User-required test checklist:**

| Requirement | Where |
|---|---|
| Reference dataset with independent expected values | `tests/analytics_reference.py` plus `test_analytics.py`. Every weekly metric and product metric is hand-derived in comments |
| Property tests | `hypothesis`: metric identities, additivity, ratio-of-sums, 200 examples each |
| Empty business | `test_empty_business_has_no_analytics` |
| Single-order customers, segments near thresholds | 13 boundary cases in `test_lifecycle_thresholds` (exact 2x and 4x gaps, 180 days, 60-day history) plus the reference customers |
| Duplicate order lines | counted once (`test_duplicate_order_lines_are_counted_once`) |
| Fully refunded orders, partial returns, zero revenue | reference O3 and R3, `test_fully_refunded_and_zero_revenue_margin_is_undefined` |
| Missing product costs | reference P3, ingestion V-045, restrictions, null margins |
| Period boundaries | Sunday/Monday ISO weeks, inclusive ranges, equal-period arithmetic |
| Customer ordering patterns | lifecycle cases (single, pairs, regular, overdue, lapsed, same-day orders) |
| Extreme values | 10¹⁴-scale exact sums through the full upload path |
| Small categories | `low_support` skips counted in the detection summary. Product coverage rule tested |
| Clean data has no incidents | 5 seeds: zero incidents, with 298–327 slices actually tested |
| Planted abnormal returns | 5 seeds: the planted product or category is detected with evidence. Every source reference resolves to a stored row, hashes verify, and the defective-reason share rises |
| No fabricated incidents | materiality test (statistically anomalous but immaterial gives no incident). Static guard: no generator or demo vocabulary in `app/`. No LLM exists in the pipeline |
| Reproducibility | two recomputations give identical hashes, and they match the persisted incidents |

### Errors encountered and fixes applied

1. Stale statistics led to pathological plans (24–27 s). Fixed with AMD-11 statistics refresh plus the window-function rewrite.
2. Segment insert round trips (1,090 statements). Fixed with COPY plus `INSERT … SELECT`.
3. Dates in incident JSON were not serialisable. Every JSONB payload now goes through the canonical encoder used for hashing, so the stored and hashed forms are identical.
4. An empty period produced NULL sums, which crashed with a 500. Every SQL `SUM` is now `COALESCE`d.
5. The default range ended at T and silently omitted refunds dated after the last order. Defaults now end at the last activity (AMD-11).
6. Test-design corrections, made **without touching thresholds**:
   - Seed 8 and seed 11 spikes were statistically anomalous but immaterial (impact 3,444 against materiality 3,674; 2,000 against 5,033). That is correct behaviour.
   - The generator now plants spikes on the top product by revenue, and a dedicated test asserts that immaterial anomalies are not reported.
   - A broad customer segment can legitimately outrank the planted product, so the test requires the planted slice to be among the refund incidents.
7. Hand-arithmetic errors in two test expectations (C1 monetary as of T; the 10¹⁴ COGS) were found by the tests and corrected. The product was right.

### Known limitations and remaining risks

| Item | Note |
|---|---|
| CI unobserved | As above |
| Static product cost (A4) | Margins over long periods use today's cost |
| Overlapping incidents across dimensions | Broader slices (for example a customer segment) can rank above the specific product because impact is not additive. Root-cause attribution belongs to the hypothesis phase |
| Multiple-comparison risk | About 300 slice tests per run. 5 clean seeds gave 0 false positives under config v1, but calibration over more seeds and real data remains |
| Segment as-of uses T | Customers whose first order is near the dataset start look newer than they are (left censoring). Documented |
| No charts yet | Tables only. Charts with table alternatives are deferred (AMD-10) |
| Analysis CPU in the API process | About 2 s at 100k lines. Acceptable at the target scale |

### Acceptance criteria (baseline AC-5.x and AC-6.x as scoped by AMD-10, plus the user gate)

| AC | Status | Evidence |
|---|---|---|
| AC-5.1 Golden tests for metrics | Met | reference dataset (M01–M19, M21) and unit tests (M20 via impacts) |
| AC-5.2 Zero denominators give null | Met | unit tests per ratio |
| AC-5.3 Identical `result_hash` on recompute | Met | `test_analysis_is_reproducible` |
| AC-5.4 Injection-safe dimensions | Met | allowlist, 422 test, `slice_filter` test |
| AC-5.5 100k-line analysis ≤ 15 s | Met (2.12 s locally) | benchmark |
| AC-5.6 Charts with table alternatives, axe | Partly. Tables only (charts deferred). Axe scans run in the hardening phase | AMD-10 |
| AC-6.1 Recall ≥ 95%, false positives ≤ 1 per control | Met on 5+5 seeds (5/5 detected, 0 false positives) | detection tests |
| AC-6.2 Insufficient history gives no incidents | Met | reference and cancelled-only tests |
| AC-6.3 Evidence hash reproducible | Met | hashes verified in tests |
| AC-6.4 Verification statuses | Deferred to the hypothesis phase (AMD-10) | — |
| AC-6.5 No demo literals or generator imports | Met | static tests |
| User gate: reproducible, evidence-backed, numerically correct, tested | Met | above |

### Commit hash and GitHub push status

Recorded at the start of the next phase entry, and reported in the session summary.

### Next-phase dependencies

- The LLM and orchestration phase consumes `incidents`, `evidence_items` and `customer_segments` by revision.
- The hypothesis catalog adds evidence tools (contribution and mix decomposition) on top of `queries.aggregate`.
- The owner should confirm the CI results for the pushed commits.

---

## Phase 5 — Stateful evidence-backed investigations

- **Date:** 2026-10-17
- **Starting point:** Phase 4 commit `6b14c2a` plus Claude's uncommitted Phase 5 implementation. The user explicitly authorized auditing, finishing and committing this work with multiple coding agents. No pre-existing changes were discarded. Phase 6 was not started.
- **Scope:** AMD-12, AMD-13 and AMD-14: the remaining deterministic hypothesis/evidence core and a persistent LangGraph investigation API. Human answers, interrupts, corrections, replay UI, recovery approval and external action execution remain outside this phase.

### Historical acceptance reconciliation

The complete engineering history and all seven docs were read before the completion audit. Earlier log sections remain unchanged.

- Phase 2/3 CI evidence is still **unobserved**, not a pass. A git push or local test does not establish GitHub Actions success.
- Phase 3 ingestion timings were local, not the CI-hardware measurement required by baseline AC-4.5. CI/production performance remains unverified.
- Phase 4's "AC-6.1 met on 5+5 seeds" did not establish the required 20-seed calibration. This session expands both available planted scenarios (returns and discount) to 20 seeds each, with seed-dependent date offsets, and evaluates 20 stationary controls. Original five zero-incident regressions and their assertions remain unchanged. Other baseline generator scenarios are not implemented and are not claimed as calibrated.
- Charts were deferred by AMD-10; accessibility scans and the full dashboard remain in hardening. This session does not certify unexecuted historical gates.
- Phase 4 hash/push verification was left pending for the next phase. No prior push verification is inferred here; the Phase 5 push result is recorded below.

### Technical decisions and reasons

Three parallel coding agents audited the LLM boundary, workflow durability/API, and deterministic tools/contracts. The root agent coordinated fixes, full regression, performance, generated API contracts, documentation and Git.

- Reuse the existing API, tenant context, PostgreSQL queue and analytics functions. One LLM provider adapter, no separate LLM agents or extra services.
- Freeze the library checkpoint DDL in Alembic `0004`; a migration test compares the recorded migration list with the installed library. Runtime never calls checkpoint setup/DDL.
- Cache immutable evidence by revision, tool kind and canonical parameter hash, with per-investigation links. Required catalog evidence runs regardless of LLM selections, so model phrasing and prioritization cannot change analytical verdicts.
- Missing data is unknown, not refutation. Open context requests are persisted and returned without a human interrupt in this phase.
- Runtime and budget bounds were documented in AMD-13 before the new whole-run timeout/lock implementation. AMD-14 documents durable start-idempotency format, interpretation labels and remaining phase numbering.

### Files created / changed

- `services/api/app/investigation/`: catalog, typed tool registry, facts, verification, Critic, rendering, LLM contracts, persistence, graph, runtime, output and router.
- `services/api/app/llm/`: provider, gateway, provider-safe schema generation and three versioned prompts.
- Migration `0004_investigations.py`; domain models, permissions, application wiring, job types and typed configuration.
- Existing analytics query helpers support incident-scoped read-only breakdowns. The generator adds a deterministic discount scenario for the new hypothesis tests.
- Shared idempotency storage now renews expired keys atomically and rejects concurrently claimed active keys, rolling back domain writes on conflict.
- Investigation/unit/security/migration/performance tests and scripted model helpers. Generated `openapi.json` and frontend TypeScript types. CI enforces the existing 95% analytics, verification and Critic coverage requirements.
- Contracts §12 added; architecture/security/test-strategy amendment notes, README and environment example updated.

### Graph transitions and execution flow

```text
POST investigation (permission + Idempotency-Key)
  -> tenant lock -> replay or finished-analysis check -> investigation + event + job (one tx)
run_investigation
  -> whole-run timeout -> tenant-scoped investigation -> dedicated checkpoint connection/lock
  -> initialize -> select_incident -> generate_hypotheses -> select_tools -> retrieve_evidence
       retrieve_evidence -> select_tools (bounded extra round) | verify_evidence
       verify_evidence -> detect_missing_context -> select_incident
  -> synthesize_output -> finalize
initialize -> finalize directly for insufficient_data / completed_no_incidents
```

Each node commits its domain effects and events atomically; LangGraph then saves its checkpoint synchronously. Deterministic IDs/natural keys and persisted selections make replay after commit-before-checkpoint safe. State contains IDs, counters, enums and validated requests, not dataset rows. An investigation-level PostgreSQL session advisory lock prevents overlapping lease workers from executing the same graph concurrently.

### Tool contracts and evidence validation

Seven read-only tools use strict Pydantic argument models with forbidden extra fields:

| Tool | Purpose |
|---|---|
| `metric_breakdown` | Exact metric retrieval and contribution by allowed dimension |
| `period_comparison` | Equal-length current/previous periods |
| `return_investigation` | Reason mix, refund contributors and matured cohorts |
| `segment_analysis` | Baseline lifecycle segments and changes |
| `customer_exposure` | Customers/product exposure and concentration |
| `evidence_lookup` | Evidence already linked to this investigation and incident |
| `data_quality_check` | Missing activity, anomalies, near duplicates, cancellations and costs |

Business, dataset, revision and incident scope come from the runtime, never the model. SQL values are bound; identifiers use server allowlists. No model output is executed as code. Facts use exact Decimal arithmetic in the existing 60-digit context, including extreme ratios.

Validation: typed request -> scoped deterministic query -> numerical sanity/decomposition checks -> canonical hash and immutable evidence -> hash verification -> catalog criteria and confidence -> supported candidate selection -> narrative schema, citation/placeholder/status/direction checks -> server-rendered evidence values. A corrupt evidence item is excluded from verdicts, model inputs and source observations. LLM prose carries an interpretation label; code-generated observations remain separate.

### Security, retry and error handling

- All new routes declare permissions; all new tenant tables have RLS under AMD-3. Cross-tenant route and direct-database tests cover the new tables. Checkpoints remain worker-only in the dedicated schema.
- Imported business names, CSV text, dimension values and customer/product identifiers do not enter model requests. Twenty distinct imported injection directives are persisted via normal uploads and checked against actual captured model requests. Other-tenant and business-description sentinels are absent.
- Critic rejects unsupported causes/citations, invalid numeric fields, contradictory signed claims, Unicode digits and forged internal markers. Entity placeholders are scoped to the incident's linked evidence.
- One repair attempt for invalid model output, then deterministic fallback. Transient provider failures receive at most two retries with bounded backoff; exhausted retries open the investigation's deterministic circuit breaker. Usage/error records contain sanitized codes, not raw provider errors.
- Calls, token reservation, per-call timeout, persisted LLM deadline, evidence count, tool rounds, graph depth and starts/hour are bounded. Whole-run timeout defaults to 600 seconds and reports `workflow_timeout`; graph depth reports `workflow_limit`. Failed investigations can resume checkpoints through the retry API.
- Known-usage calls record provider usage. Unknown-usage failures consume their conservative full reservation. Output allowances are capped before sending a request; retries cannot evade budget accounting.
- Explicitly disable Anthropic OpenTelemetry and reject enabled LangSmith/LangChain tracing settings. No write-capable external tools or recovery-action tables/routes are introduced.

### Performance measured in this session

`UV_CACHE_DIR=/private/tmp/root-uv-cache uv run --no-sync pytest tests/perf -m perf -s` against isolated local PostgreSQL 18.6: **5 passed in 112.50 seconds**. Performance was measured while other test suites ran; no CI/production or 20-user p95 claim is made.

| Operation | Measurement |
|---|---|
| Investigation on uploaded generated data, deterministic | 2.367 seconds; 4 incidents, 24 evidence links, 32 events |
| Same revision, cached evidence + scripted model | 1.692 seconds; 10 model calls; identical analytical signature |
| Analytics, approximately 20k / 100k lines | 0.942 / 3.125 seconds, 25 SQL statements each |
| Ingestion, 20,271 / 101,107 lines | 3.151 / 15.245 seconds total |

### Tests executed and actual results

Test environment: isolated disposable PostgreSQL databases on a temporary local cluster, runtime `root_app`, migrations as owner; Python 3.14.8 and Node 24.22.0. Commands below ran from `services/api` or `apps/web`, with the test DB variables supplied by the session and `UV_CACHE_DIR=/private/tmp/root-uv-cache` for uv.

Final backend suite: `uv run --no-sync pytest --cov --cov-fail-under=85 --durations=10` → **538 passed, 1 opt-in live test skipped, 5 perf deselected in 538.79 seconds**, 94.45% coverage. The extreme-ratio regression and actual subprocess-restart test were added after that run collected its tests; `uv run --no-sync pytest tests/unit/test_investigation_units.py tests/investigation/test_process_restart.py --cov --cov-append --cov-fail-under=85 -q` → **95 passed in 14.34 seconds**, final combined coverage **95.89%**. Together these execute all **540 current non-live backend tests** successfully. No pending failures remain.

Required coverage commands were executed: `uv run --no-sync coverage report --include='app/auth/*,app/tenancy/*' --fail-under=95` (98%); the same command for `app/analytics/*` (96%), `app/investigation/verification.py` (100%) and `app/investigation/critic.py` (98%). No threshold was lowered. `uv sync --locked` resolved 102 packages and checked the 98 installed packages successfully. OpenAPI and generated TypeScript were regenerated twice and compared byte-for-byte with `cmp`, with no drift.

Final Ruff/format/mypy rerun after all source additions: **all checks passed**, **119 files formatted**, **116 sources strictly typed**. `gitleaks git --staged --redact --no-banner .` scanned the staged Phase 5 content and reported **no leaks**.

| Check | Actual result |
|---|---|
| `uv run --no-sync ruff check .`; `ruff format --check .`; `mypy` | All passed; 118 Python files formatted and 115 sources checked at the final pre-regression checkpoint |
| Initial `uv run --no-sync pytest --cov --cov-fail-under=85` | 476 passed, 1 failed, 4 perf deselected, 347.26 seconds. Failure: LangGraph wrapped worker cancellation; fixed and dedicated resilience rerun passed |
| Investigation integration suite | 12 passed, 92.13 seconds |
| Final dedicated resilience suite before the integrity addition | 10 passed, 74.14 seconds |
| Start idempotency/concurrency regressions | 2 passed, 10.13 seconds |
| Final TTL and corrupt-evidence regressions | 2 passed, 15.31 seconds |
| Twenty imported injection directives via real upload | 1 integration test passed, 15.42 seconds |
| LLM/Critic targeted units | 129 passed, 1.13 seconds before final extreme-ratio regression |
| Final extreme-ratio investigation units | 94 passed, 0.57 seconds |
| Actual worker SIGKILL and fresh subprocess recovery | 1 passed, 15.03 seconds; then included in the 95-test supplemental coverage run above. Checkpoints resume, facts match and events/evidence links are not duplicated |
| 20-seed control calibration (`--junitxml=... -o junit_family=xunit1`) | Passed in 109.55 seconds. 3 incidents/20 controls = 0.15 average, below the unchanged ≤1 target. Seeds 909 and 1818 produced 1 and 2 respectively; other 18 produced zero |
| `npm run lint`; `npm run typecheck`; `npm run test:coverage` | Passed. 30 frontend tests; statements 97.71%, branches 86.95% |
| `BACKEND_URL=... npm run build -- --webpack` | Passed. Default Turbopack build failed because this host blocks its worker socket even with sandbox escalation; webpack is a supported compiler, no source or threshold change |
| `CI=1 npx playwright test` | 7 passed, 26.2 seconds, production web build + API + worker + isolated PostgreSQL |
| `uv run --no-sync pip-audit` | No known vulnerabilities |
| `npm audit --omit=dev --audit-level=high`; `npm run audit:gate` | Zero production findings; gate passed with the existing AMD-7 exception only |
| Checksum-verified gitleaks 8.30.1 full-history scan | 5 commits scanned, no leaks; staged scan recorded with commit below |
| Separate live-model integration | **Not run:** no `ANTHROPIC_API_KEY` and no explicit `ROOT_LIVE_LLM_TESTS=1`. The collected test is opt-in under AMD-13 |

### Errors encountered and fixes applied

- Claude's initial tree had one lint failure and eleven strict-type errors; fixed without lowering gates.
- Gateway timeout previously ignored remaining investigation time; token budgets could overshoot on a final call and failures charged zero. Fixed with bounded calls/sleeps, conservative reservation and failure accounting.
- Committed node selections/circuit state could be lost before checkpoint; persisted state is now reused. LangGraph's `NodeCancelledError` is converted back to cancellation so lease recovery works rather than marking a crashed worker's run permanently failed.
- Concurrent starts could race quota/deduplication. Added serialization and durable start-key replay, including replay after completion and expiry.
- Invalid evidence hashes were excluded from verification but could still reach prompts/output. All default evidence reads now exclude them; verification explicitly loads invalid rows only to record the integrity event.
- Incident data-quality zero-day checks were not slice-scoped; zero-price customers were omitted from exposure; extreme valid ratios used the default Decimal precision. Fixed with regression tests.
- The new 20-control test initially asserted zero incidents on every control, stricter than the published average ≤1 gate. That newly written test design was corrected to the actual contract: original five zero-control assertions remain unchanged, and a separate 20-control average calibration was added. No detector constants, acceptance threshold or existing test assertion was weakened. The superseded design run recorded 61 passed/2 failed; the corrected calibration passed.
- Sandbox initially blocked PostgreSQL shared memory/localhost. An isolated temporary cluster and approved local test runs resolved this. JUnit calibration initially needed `junit_family=xunit1` under warning-as-error; the corrected command passed. Frontend typecheck/build first lacked `BACKEND_URL`; rerunning with the required configuration resolved typecheck.

### Known limitations and remaining risks

- Live provider behavior and latency are unverified in this session. The official [provider migration guide](https://platform.claude.com/docs/en/models/opus-5-5/migration-guide) and [structured-output reference](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) were checked; pinned releases were rechecked on PyPI: [anthropic 1.15.0](https://pypi.org/project/anthropic/1.15.0/), [langgraph 1.2.15](https://pypi.org/project/langgraph/1.2.15/), [checkpoint-postgres 3.2.0](https://pypi.org/project/langgraph-checkpoint-postgres/3.2.0/). This establishes documented compatibility, not a live pass.
- Arbitrary prose is not a semantic proof of a claim. AI interpretation is explicitly labelled; facts, observations, verdicts and candidate ranking are deterministic and evidence-linked.
- Three control incidents across 20 seeds demonstrate that deterministic detection is not a zero-false-positive guarantee. Broader real-data calibration and unimplemented baseline scenarios remain outstanding.
- CI run results, production measurements, 500k-line/20-user load tests, charts/axe, account lifecycle and retention remain unverified or deferred; no claim of full-product readiness is made.

### Acceptance criteria status

Phase 5 implementation and all local completion gates are **met**, under AMD-12/13/14. Git verification follows in the post-commit record; remote CI and the optional real-provider run are not claimed as passed.

| Criterion | Status and evidence |
|---|---|
| Uploaded data produces evidence-backed output | Met: planted return/discount incidents, source observations, independently resolved citations and supported hypotheses |
| Strict read-only tool selection and tenant isolation | Met: typed schemas, argument rejection, automatic role/route matrix, cross-tenant/RLS tests |
| Competing hypotheses, deterministic statuses and unresolved explanations | Met: catalog applicability/status/confidence tests; missing evidence remains inconclusive; context requests persisted |
| Unsupported citations and impossible numerical claims rejected | Met: Critic tests, malformed model output, overclaimed causes, impossible tool results and corrupt stored evidence |
| Injection resistance | Met: 20 persisted CSV directives, business/foreign-business sentinels, no raw source text or side-effect tools in actual model inputs |
| Provider/tool failure, retries and limits | Met: timeout/rate-limit/unavailability/tool failures, repair/fallback, token/call/deadline/depth/tool-loop limits |
| PostgreSQL durability and restart | Met: checkpoint schema/state tests, commit-before-checkpoint replay, concurrent workers, actual SIGKILL and fresh-process resume |
| Facts independent of model wording | Met: deterministic and three scripted styles have identical analytical signatures |
| Quality gates and full regression | Met locally: 540 distinct backend tests passed, opt-in live test skipped, 5 perf tests passed; required coverage/lint/types/audits, 30 frontend tests and 7 browser tests passed |
| No Phase 6 or unauthorized external effects | Met: no human-answer/recovery/executor implementation, no write-capable external model tools |

### Commit hash and GitHub push status

- Implementation commit hash and verified push result are appended in the post-commit verification record below, avoiding a self-referential commit hash.
- Before Phase 5 commit, `git ls-remote origin refs/heads/main` returned `6b14c2ac1f7d765ea82d88d056e9e4e8e9528c88`, matching the Phase 4 local base. This verifies its remote presence now, not historical CI success.

### Next-phase dependencies

- Do not begin Phase 6 in this session.
- A later user-authorized Phase 6 implements the human-in-the-loop scope (baseline Phase 8) against persisted context requests, evaluations and graph checkpoints. It must preserve earlier evidence and establish answer/resume durability; no answer endpoints are stubbed here.

### Post-commit verification record

- **Implementation commit:** `322c3fa329e875bcea58a5568f0cc32b5e28e875` — `feat: Phase 5 stateful evidence-backed investigation engine`. Uses the repository owner's configured Git identity; no AI co-author or generated-by trailers.
- **Push:** `git push origin main` succeeded, advancing `6b14c2a..322c3fa` without force.
- **Verification:** `git ls-remote origin refs/heads/main` and `git rev-parse HEAD` both returned `322c3fa329e875bcea58a5568f0cc32b5e28e875`. Working tree was clean after the implementation commit.
- **CI:** GitHub Actions results remain **unobserved** in this session. Local checks and remote hash verification are not presented as CI success.
- **Cleanup:** stopped only the isolated temporary PostgreSQL cluster created for this audit. No existing database service was stopped or modified.
- This verification record is appended in a documentation-only follow-up commit; earlier log entries and test evidence are unchanged. Application tests are **not rerun for this bookkeeping-only addition** because no application, migration, dependency, generated contract or test code changes.
