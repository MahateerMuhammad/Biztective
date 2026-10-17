# ROOT 2.0 — Test Strategy

> Baseline created in Phase 1. Test IDs (T-xx) are referenced by [ROOT_MASTER_PLAN.md §10](ROOT_MASTER_PLAN.md) and [SECURITY_MODEL.md §8](SECURITY_MODEL.md).
> **Rule:** a test is reported as passed only after it has actually been executed. The exact command and its output summary go in the engineering log (AGENTS.md R-5).

## 1. Principles

1. **Test facts, not phrasing.** Financial metrics, detection, verification and estimates are tested with exact, hand-computed golden values (Decimal equality).
2. **Prove safety properties negatively.** For every control there is a test showing the forbidden thing does not happen: no cross-tenant read, no execution after rejection, no duplicate external write.
3. **No hardcoding.** Detection is tested on generated datasets with random seeds, entity names and date offsets (§6).
4. **Real database.** Integration and security tests run against a real PostgreSQL with RLS and the `root_app` role, not SQLite and not mocks.
5. **External services are faked at the HTTP boundary.** LLM, Sheets and Slack adapters are tested with `respx` and recorded contract fixtures. One manual real-Sheets verification happens per release (AC-11.5).
6. **Determinism.** Tests are hermetic (fixed seeds, fixed `T`, no wall-clock dependence except in auth expiry tests, which use an injectable clock).
7. **Never weaken a gate to pass.** Lowering a threshold requires a master-plan amendment explaining why (AGENTS.md R-6).

## 2. Test levels

| Level | Tooling | Scope | Location |
|---|---|---|---|
| Unit | pytest, Hypothesis (property-based) | pure functions: metrics math, RFM, detection statistics, verification, estimator, validators, placeholder renderer, canonical JSON | `backend/tests/unit` |
| Integration | pytest + PostgreSQL | repositories, SQL queries, RLS, jobs, LangGraph with checkpointer, API routes via `httpx.AsyncClient` | `backend/tests/integration` |
| Security | pytest + PostgreSQL | authz matrix, tenant isolation, CSRF, injection corpora, secrets, headers | `backend/tests/security` |
| Contract | pytest + respx | LLM gateway, Sheets and Slack adapters against fixtures. OpenAPI ↔ TS types drift | `backend/tests/contract` |
| Performance | pytest benchmark scripts (timed, recorded) | ingest and analysis at 100k and 500k lines | `backend/tests/perf` (marker `perf`, CI nightly or manual) |
| Frontend unit and component | Vitest + Testing Library | components, hooks, formatters, approval dialog | `frontend/tests` |
| End-to-end | Playwright (+ `@axe-core/playwright`) | user journeys, demo journey, accessibility | `frontend/e2e` |

## 3. Environments and data

- **Local:** Docker Compose Postgres, `uv run pytest`, `npm test`, `npx playwright test`.
- **CI:** GitHub Actions `services: postgres` (same major version as production). Migrations run as `root_owner` and tests run as `root_app`.
- **Test data:** factory functions and the generator (§6). No production data in tests.
- **LLM in CI:** a deterministic fake provider (recorded responses plus adversarial fixtures). Real-provider runs are manual (Phase 7 AC-7.1) and recorded.

## 4. Quality gates (CI must be green to merge or push to `main`)

> **Amended by AMD-21 (Phase 10):** CI enforces separate ≥95% coverage gates for `app/auth/`, `app/tenancy/`, `app/analytics/`, `app/investigation/verification.py`, `app/investigation/critic.py`, `app/recovery/`, and `app/actions/` (the implemented integrations and executor). The dependency audit gate fails closed on registry errors and malformed reports. Performance tests include 20 concurrent authenticated reads and an exactly 500,000-line uploaded dataset; measurements and limitations are recorded in the Phase 10 engineering log. Live external tests require explicit authorization of their destination and writes.

> **Amended by AMD-15 (Phase 6):** T-25/T-26/T-28 are implemented for the clarification interrupt only (`tests/investigation/test_hitl.py`, `tests/investigation/test_process_restart.py`, `tests/unit/test_hitl_units.py`); corrections (T-27), rejection and findings review stay with Phase 7. The browser test `apps/web/e2e/investigation.spec.ts` uses the seeded generator through `uv` (dev tooling, never runtime code).

> **Amended by AMD-13 (Phase 5 completion audit):** the LLM output validator is implemented at `app/investigation/critic.py`; its coverage gate is ≥95%, as is `app/investigation/verification.py`. Phase 5 is backend-only and has no human interrupts or timeline UI. The real-provider integration test is opt-in (`ROOT_LIVE_LLM_TESTS=1` with `ANTHROPIC_API_KEY`); deterministic and fake-provider end-to-end investigations are required in CI. Unrun live tests must be reported as such. Original detection calibration and coverage thresholds remain unchanged.

| Gate | Threshold |
|---|---|
| ruff (lint + format check, including `S` security rules) | 0 findings |
| mypy `--strict` on `backend/app` | 0 errors |
| pytest (unit, integration, security, contract) | 100% pass. No `xfail` without a linked amendment |
| Backend line coverage | overall ≥ 85%. `analytics/`, `investigation/verification.py`, `recovery/`, `tenancy/`, `auth/`, `integrations/`, `llm/validation.py` ≥ 95% |
| `tsc --noEmit` (strict) | 0 errors |
| ESLint (including `react/no-danger`) | 0 errors |
| Vitest | 100% pass. Frontend `lib/` coverage ≥ 80% |
| Playwright smoke (per push) / full demo journey (Phase 12+) | 100% pass |
| axe (Phase 5+) | 0 serious or critical |
| pip-audit, npm audit `--audit-level=high` | 0 findings (an exception requires an amendment with a CVE analysis) |
| gitleaks | 0 findings |
| OpenAPI types drift | generated `frontend/lib/api/schema.d.ts` matches the committed file |

## 5. Test catalog

| ID | Level | Description | Covers | Phase |
|---|---|---|---|---|
| T-01 | Unit | Golden fixtures for M01–M21, hand-computed in the test file, including returns across periods, restockable vs written-off COGS and partial refunds | FR-05, NFR-01 | 5 |
| T-02 | Unit (property) | Invariants: net_sales = gross − discounts; 0 ≤ discount_rate ≤ 1; Σ slice values = overall for additive metrics; ratio = Σnum/Σden; zero denominators → null | FR-05 | 5 |
| T-03 | Unit | RFM scoring with ties, segment precedence for every rule, each risk flag at its boundary, low_sample flag | FR-06 | 5 |
| T-04 | Unit | Detection: true positives per metric, stationary-noise negatives, MAD = 0 fallback, minimum support, materiality, partial-week exclusion, parent suppression, overlap merge, ranking tie-breaks, insufficient data | FR-07 | 6 |
| T-05 | Unit + integration | Generator determinism (same seed → identical bytes). Anti-hardcoding suite: 20 seeds × each scenario with randomised entity names and date offsets → AC-6.1 recall and false-positive targets. Static check that runtime code imports no generator and contains no demo literals | FR-07, FR-29, NFR-01 | 4, 6 |
| T-06 | Unit | Hypothesis applicability (≥ 2 competing candidates per incident, `H_DATA_QUALITY` and `H_EXTERNAL_CONTEXT` always included). Verification criteria and status rules for every catalog template, reaching all statuses, and confidence arithmetic | FR-08, FR-10 | 6 |
| T-07 | Unit | Evidence tool parameter models reject unknown dimensions, out-of-range k and extra fields. Identifier allowlist mapping | FR-09, SEC-08 | 6 |
| T-08 | Integration | Reproducibility: recompute revision → identical `revision_hash`. Re-execute evidence → identical `result_hash` | NFR-01, FR-05, FR-09 | 5, 6 |
| T-09 | Integration | Every validation rule V-001…V-099 with a failing fixture → expected code, severity and dataset status. Happy path → `ready` | FR-03, FR-04, SEC-07 | 4 |
| T-10 | Security | Upload abuse: oversize (streamed rejection before full read), too many rows, NUL bytes, invalid UTF-8, wrong content type, path-traversal filenames, header-only sales, 5% quarantine threshold | SEC-07, FR-04 | 4 |
| T-11 | Security | PII-suspect columns dropped: after upload, no table contains the sentinel values from those columns | SEC-22 | 4 |
| T-12 | Integration | Register, login, logout, me. Argon2id hash format. Password policy boundaries | FR-01, SEC-01 | 3 |
| T-13 | Security | Session fixation (token changes on login), idle and absolute expiry (injected clock), logout revocation, cookie attributes | SEC-02, FR-01 | 3 |
| T-14 | Security | Unsafe methods without, with wrong, or with another session's CSRF token → 403. Bad `Origin` → 403 | SEC-03 | 3 |
| T-15 | Security | Throttling thresholds per account and per IP. Uniform error body for unknown and known users. Timing within tolerance | SEC-04, SEC-20 | 3 |
| T-16 | Security | Authorization matrix: enumerate all routes from the app. Each must declare a permission. Each role × route returns the expected allow/deny per SECURITY_MODEL §5.1 | SEC-05, FR-02 | 3+ (re-run every phase) |
| T-17 | Security | Cross-tenant IDOR: for each tenant route, a user of business A accessing B's resource IDs → 404, with no data in the body and no side effects (DB unchanged) | SEC-06 | 3+ |
| T-18 | Security | RLS: as `root_app` without tenant context → 0 rows on every tenant table. With context A → only A's rows. Insert with a mismatched business_id → error | SEC-06 | 3+ |
| T-19 | Security | SQL-injection corpus in dimension, metric, sort, filter and cursor parameters → 422. Query log shows no injected SQL | SEC-08 | 5 |
| T-20 | Security | Response headers (CSP, HSTS in production mode, nosniff, frame-ancestors, referrer, COOP, no-store) on API and frontend. ESLint ban on `dangerouslySetInnerHTML`. Rendering LLM text containing `<script>` displays it inertly (Playwright) | SEC-09, SEC-10 | 2, 5 |
| T-21 | Security | Sentinel secrets (webhook URL, verification code, session token, password) never appear in API responses or captured logs. `integrations.secret_ciphertext` ≠ plaintext and decrypts correctly. Key rotation path | SEC-11, SEC-24 | 11 |
| T-22 | Security | Prompt-injection corpus (≥ 20) via `reason_text`, human free text and dimension values: instructions to approve actions, reveal data, change numbers, call unknown tools or emit HTML. Assert: schema-valid or fallback, no non-allowlisted tool executed, no action created or approved, no customer IDs in LLM inputs. LLM-proposed templates outside the catalog are rejected | SEC-12, SEC-22, FR-08 | 7 |
| T-23 | Unit | Output validator: digits outside placeholders rejected; raw entity names rejected; unknown evidence IDs rejected; repair then fallback path; renderer formats values from evidence exactly | FR-16, SEC-12 | 7 |
| T-24 | Integration | LLM provider errors and timeouts on every task → the deterministic fallback completes the investigation and `llm.fallback_used` is recorded | NFR-03 | 7 |
| T-25 | Integration | HITL durability: for each interrupt type, answer → kill the worker before resume → restart → the graph resumes exactly once, with no duplicate rows. A worker killed mid-node resumes from the checkpoint | FR-11, NFR-04 | 7, 8 |
| T-26 | Integration | Context answer creates `human_context` evidence attributed to the user and changes the target hypothesis status per catalog rule | FR-12 | 8 |
| T-27 | Integration | Correction → new overlay version and revision. The original revision's metric, evidence and revision hashes are unchanged. Incidents are recomputed. Prior human inputs carry over by incident key | FR-13 | 8 |
| T-28 | Integration | Hypothesis rejection re-ranks and never yields a rejected root cause. Findings challenge loops ≤ 2, then `disputed`. Clarification caps enforced | FR-14, FR-15 | 8 |
| T-29 | Integration | Replay: projection(final seq) == live state. Projection at each seq is deterministic. Zero LLM calls and zero analytics queries during replay (spies) | FR-17, NFR-01 | 9 |
| T-30 | Integration | Revision compare on a correction scenario lists the expected added, removed and changed items with signed deltas. Re-verify match and tamper-mismatch | FR-18 | 9 |
| T-31 | Unit | Constraint validator (each constraint type) and estimator golden tests per strategy and scenario | FR-19 | 10 |
| T-32 | Security | Analyst approve → 403. Viewer → 403. Stale hash → 409. Edit after approval revokes the approval. Executor refuses when the hash mismatches, the approver has since been demoted, the integration is revoked, or the business mismatches | SEC-13, FR-20 | 10, 11 |
| T-33 | Security | Rejected and revoked actions: zero jobs, zero `action_executions`, zero adapter HTTP calls (respx asserts no request) | SEC-13 | 10, 11 |
| T-34 | Integration | Exactly-once effect: (a) double approve with the same Idempotency-Key, (b) owner retry after a transient failure, (c) crash after the HTTP call before the outcome commit, (d) two concurrent workers → exactly one tab or row set. Slack ambiguity → `outcome_unknown` with no automatic resend | SEC-14, FR-21, FR-24, NFR-04 | 11 |
| T-35 | Contract | Sheets adapter: RAW input option, formula escaping for `= + - @ \t \r`, cell length cap, row cap and truncation note, backoff honouring `Retry-After`, 403 mapping, reconciliation finds an existing tab or key | FR-21, SEC-16, NFR-03 | 11 |
| T-36 | Security | Verify fails with a wrong, expired or reused code. Binding the same spreadsheet to a second business → 409 `integration_already_bound` without revealing the other tenant | FR-22, SEC-17 | 11 |
| T-37 | Security | Slack URL validation: other hosts, http, IP literals, `hooks.slack.com.evil.com`, userinfo tricks, redirects → rejected or not followed | FR-23, SEC-15 | 11 |
| T-38 | Security | `audit_log` UPDATE and DELETE as `root_app` → error. Each audited action in SECURITY_MODEL §9 produces exactly one entry with correct actor and target | FR-25, SEC-18 | 3+ |
| T-39 | Security | 404, 405, 413, 415, 422 and forced 500 → problem+json, no stack trace, SQL or paths. request_id present | SEC-19 | 2 |
| T-40 | Security | Upload, investigation-start and integration-test quotas → 429 with `Retry-After`. Global limiter | SEC-20 | 4, 12 |
| T-41 | CI | Lint, type, audit and gitleaks gates are configured and fail on seeded violations (verified once by a deliberate failing branch, recorded in the log) | SEC-21, NFR-06 | 2 |
| T-42 | Integration | Retention sweep purges per SECURITY_MODEL §10. Business deletion removes all tenant rows (query every table) and checkpoints. Dataset deletion rules | FR-28, SEC-23 | 4, 12 |
| T-43 | E2E | Full demo journey (DEMO_PLAN §4) on a generated dataset with fake LLM and fake Sheets in CI. Real services in rehearsal. Timed ≤ 6 min | NFR-08, J6 | 12, 13 |
| T-44 | E2E | axe scans on sign-in, upload, dashboard, investigation, clarification, plan, replay and integrations. Keyboard-only completion of answer and approve flows | NFR-05, FR-27 | 5, 12 |
| T-45 | Perf | Ingest and analysis timings at 100k and 500k lines vs NFR-02. API p95 under 20 concurrent users | NFR-02 | 4, 5, 12 |
| T-46 | Frontend | Components: approval dialog shows the exact payload and hash prefix; event timeline ordering and polling stop on terminal state; metric charts have table alternatives; error and empty states | FR-20, FR-26, FR-27 | 5, 7, 8, 10 |
| T-47 | Security | CORS: disallowed origins get no ACAO header. Credentials only for `FRONTEND_ORIGIN`. Unsafe request with a foreign Origin → 403 | SEC-25 | 2, 3 |
| T-48 | Integration | Memberships: add, change and remove roles. Last-owner protection. Removed member's access revoked immediately. Idempotency-Key replay and reuse semantics | FR-02, SEC-14 | 3 |
| T-49 | Integration | Events API: ordering, `after_seq` cursor, gap-free seq under concurrent writers, tenant scoping | FR-26 | 7 |
| T-50 | Integration | LLM budget: exceeding the call or token limit → deterministic mode, recorded | NFR-10 | 7 |
| T-51 | Integration | `/healthz`, `/readyz` (DB down → 503). JSON logs contain request_id, business_id and investigation_id where applicable | NFR-07, SEC-24 | 2 |
| T-52 | Integration | Dataset immutability: UPDATE on `ready` dataset rows denied (trigger and privileges). A new upload creates version N+1 | FR-03 | 4 |
| T-53 | Deploy | Production smoke (health, login, upload, start investigation) and rollback rehearsal timing | Deployment, AC-13.x | 13 |
| T-54 | E2E | Cross-browser smoke (Chromium, Firefox, WebKit) at 1280 px and 375 px | NFR-09 | 12 |

## 6. Synthetic data generator and anti-hardcoding

`backend/scripts/generate_dataset.py` (dev-only, FR-29):

- **Inputs:** `--seed`, `--weeks` (≥ 16), `--customers`, `--products`, `--start-date`, `--scenario` (repeatable), `--name-pool` (random or fixed), `--noise` (malformed-row rate and PII-like decoy column, for validation demos), `--out`.
- **Baseline process:** weekly seasonality, Poisson order counts, customer purchase cycles (gamma-distributed gaps), category price bands, background returns of 3–6% with a realistic reason mix, sporadic discounts.
- **Injectable scenarios**, each with an onset week and magnitude:

| Scenario | Effect in data | Expected detection |
|---|---|---|
| `quality_defect` | One product's `defective`/`damaged` returns rise | `refund_rate` incident on the product or category. `H_RETURNS_QUALITY` supported |
| `fulfillment_delay` | `late_delivery` returns plus lower repeat purchases in one region | `refund_rate` or `repeat_customer_rate` in the region. `H_FULFILLMENT` |
| `discount_leak` | Discounts in one channel rise | `discount_rate` and `gross_margin_pct`. `H_PRICE_DISCOUNT` |
| `churn_wave` | A share of loyal or at-risk customers stops ordering | `active_customers` and `net_revenue`. `H_CUSTOMER_CHURN` |
| `test_orders` | Internal high-value orders from one customer in the baseline inflate it | Spurious `net_revenue` drop. `H_DATA_QUALITY` (CT-3) |
| `promo_baseline` | A one-off promotion spike in the baseline | Needs a human `exclude_baseline_period` correction |
| `control` | None | No incidents expected (false-positive measurement) |

- **Anti-hardcoding rules:** runtime code never imports the generator. The test suite randomises names and dates. A static test greps `backend/app` for the demo name pool and fixed demo dates and fails on any match.
- The demo dataset is produced by the generator with a recorded seed and uploaded through the public API (DEMO_PLAN §2).

## 7. CI pipeline (`.github/workflows/ci.yml`, Phase 2)

```
on: push, pull_request
jobs:
  backend:  setup uv → uv sync --locked → ruff → mypy → alembic upgrade (root_owner) → pytest (root_app) + coverage gate → pip-audit
  frontend: npm ci → typecheck → eslint → vitest + coverage → build → openapi drift check
  e2e:      needs backend+frontend → start Postgres, backend, frontend → playwright (smoke; full journey from Phase 12)
  security: gitleaks (full history on main)
  perf:     workflow_dispatch / nightly → T-45 → artifact with timings
```

Actions are pinned by commit SHA. Caches are keyed by lockfile hashes.

## 8. Recording test evidence

Each phase log entry includes:

- The exact commands run.
- Pass, fail and skip counts.
- Coverage figures.
- Any failures and their fixes.
- CI run URL (once CI exists).
- For manual verifications (real LLM, real Sheets), the date, the environment and what was observed.

Unexecuted tests are listed as **not run**, with the reason.
