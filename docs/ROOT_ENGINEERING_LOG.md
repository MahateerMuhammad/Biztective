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
