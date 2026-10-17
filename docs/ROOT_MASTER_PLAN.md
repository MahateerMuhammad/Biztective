# ROOT 2.0 — Master Plan

> **Status:** Baseline v1.0, created in Phase 1 (2026-10-16).
> **Change policy:** This document is *immutable to history*. Sections 1–12 are never rewritten.
> Changes are appended to **§13 Amendments** with a date, the phase, the reason and the affected IDs.
> An amendment overrides the baseline only where it says so explicitly.

Companion documents:

| Document | Purpose |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, investigation lifecycle, algorithms, LangGraph, deployment, ADRs |
| [DATA_CONTRACTS.md](DATA_CONTRACTS.md) | CSV contracts, metric definitions, segmentation, database schema, API, errors, LLM I/O, events |
| [SECURITY_MODEL.md](SECURITY_MODEL.md) | Security requirements (SEC-xx), threat model, permissions, retention |
| [TEST_STRATEGY.md](TEST_STRATEGY.md) | Test catalog (T-xx), quality gates, CI |
| [DEMO_PLAN.md](DEMO_PLAN.md) | Six-minute judging demonstration |
| [ROOT_ENGINEERING_LOG.md](ROOT_ENGINEERING_LOG.md) | Cumulative per-phase engineering journal |
| [../AGENTS.md](../AGENTS.md) | Mandatory rules for every coding-agent session |

---

## 1. Product vision

ROOT 2.0 is an autonomous business investigation and recovery platform. A business uploads sales, products, customers and returns data. ROOT then:

1. Computes exact, reproducible financial metrics.
2. Detects material business problems ("incidents") with deterministic statistics.
3. Investigates competing explanations ("hypotheses") with traceable evidence.
4. Asks humans for context that the data cannot contain, and accepts corrections where the data is wrong.
5. Revises its conclusions visibly, keeping every earlier version.
6. Proposes recovery strategies bounded by human-set constraints and assumptions.
7. Executes only explicitly approved actions through Google Sheets and, optionally, Slack.

**Theme alignment: Human + AI, Better Together.** Humans change the investigation itself, not just approve its output:

| Human input | What it changes in the system |
|---|---|
| Context answer ("we changed supplier in week 38") | Adds attributed `human_context` evidence, which changes hypothesis status and confidence and reroutes the graph |
| Data correction ("these are internal test orders") | Creates a data overlay and a new analysis revision. Incidents, impacts and evidence are recomputed and the original revision is preserved |
| Hypothesis rejection | Removes a line of inquiry and forces re-ranking. The rejection reason is kept as evidence |
| Findings challenge | Sends the investigation back to hypothesis generation, directed by the human |
| Plan constraints and assumptions | Bound strategy selection and drive the deterministic impact estimates |
| Action edits, approval or rejection | Decide which side effects happen. A rejection guarantees no side effect |

Investigation Replay makes this influence visible: what ROOT believed before each human input, and what changed afterwards.

## 2. Personas

| ID | Persona | Role in ROOT | Goals | Pain points |
|---|---|---|---|---|
| P1 | **Owner / Founder** of a small or medium online retailer | `owner` | Understand why revenue or profit fell and act quickly and safely | No analyst team. Dashboards show *what* changed, not *why* |
| P2 | **Operations / Growth Analyst** | `analyst` | Run investigations, supply context, correct data, draft plans | Spends days slicing spreadsheets. Findings are hard to defend |
| P3 | **Stakeholder / Advisor** (accountant, investor, team member) | `viewer` | Read conclusions and their evidence | Cannot verify claims made by people or AI |
| P4 | **Competition judge** | Uses the demo `owner` account | Watch an end-to-end, credible, human-shaped workflow in six minutes | Generic chatbots and hardcoded demos |

## 3. Core use cases

| ID | Use case | Primary persona |
|---|---|---|
| UC-01 | Register, sign in, create a business, invite members with roles | P1 |
| UC-02 | Upload four CSV files and review the validation report | P1, P2 |
| UC-03 | View the metric dashboard and customer segments | P1, P2, P3 |
| UC-04 | Start an investigation and watch it progress live | P1, P2 |
| UC-05 | Review ranked incidents, hypotheses and evidence | P1, P2, P3 |
| UC-06 | Answer clarification questions | P1, P2 |
| UC-07 | Correct data (exclude records, reclassify return reasons, exclude baseline periods) | P1, P2 |
| UC-08 | Reject a hypothesis, or accept or challenge findings | P1, P2 |
| UC-09 | Set recovery constraints and assumptions, then review the proposed plan | P1, P2 |
| UC-10 | Edit, approve or reject individual actions | P1 approves; P2 edits |
| UC-11 | Execute approved actions to Google Sheets or Slack and retry failures | P1 |
| UC-12 | Replay an investigation and compare revisions | All |
| UC-13 | Connect Google Sheets (with ownership verification) and Slack | P1 |
| UC-14 | View the audit log, and delete datasets or the business | P1 |

## 4. Primary user journeys

**J1: First investigation (happy path).** Sign in → create business → upload four CSVs → validation report (accepted with warnings) → dashboard → *Start investigation* → live timeline: metrics, incidents, hypotheses, evidence → clarification question → answer → hypotheses re-evaluated → findings draft → accept → set constraints and assumptions → plan → approve two actions, reject one → Sheets tab and Slack message created → investigation completed → Replay.

**J2: Correction path.** During J1 the human marks suspicious orders as internal test orders → overlay v1 → new analysis revision → incident impact recomputed → the revision diff shows the change → the investigation continues on the new revision.

**J3: Disagreement path.** The human rejects the top hypothesis ("no promotion ran") → the next hypothesis is promoted or a new clarification is asked → the findings are challenged once → bounded re-investigation → accepted.

**J4: Failure and recovery path.** The Sheets API returns 503 → the action is `failed` with a recoverable error → the owner retries → the action succeeds exactly once (idempotency).

**J5: Degraded path.** The LLM provider is unavailable → the investigation completes in deterministic mode with templated narratives and catalog-ranked hypotheses, clearly labelled.

## 5. Scope

### 5.1 In scope

- Multi-tenant web application: one FastAPI backend service and one Next.js frontend.
- CSV ingestion of the four entities defined in DATA_CONTRACTS §2.
- Deterministic metrics, segmentation, incident detection, evidence tools and hypothesis verification.
- LangGraph-orchestrated investigations with durable PostgreSQL checkpoints and human interrupts.
- LLM use limited to hypothesis proposal within a catalog, evidence-tool selection from an allowlist, question phrasing, narrative synthesis and strategy selection within a catalog.
- Investigation Replay and revision comparison.
- Recovery planning, approval workflow, execution to Google Sheets and optional Slack webhook.
- Audit log, retention, deletion.
- Deployment to Vercel (frontend) and Railway (backend and managed PostgreSQL). Render is the documented alternative.

### 5.2 Explicit non-goals

| ID | Non-goal | Reason |
|---|---|---|
| NG-01 | Real-time streaming ingestion and direct e-commerce connectors (Shopify and similar) | CSV covers the competition need. Connectors would add OAuth surface area |
| NG-02 | Forecasting, ML model training, vector search or RAG | Not needed for deterministic diagnosis. Would add infrastructure |
| NG-03 | Autonomous execution without approval, or LLM-triggered side effects | Violates the safety model |
| NG-04 | Multi-currency and FX conversion, tax and shipping accounting | Out of scope. Documented as assumptions A1 and A2 |
| NG-05 | Historical (time-varying) product costs | Contract supplies one cost per product. Limitation A4 |
| NG-06 | Storing direct customer PII (names, emails, phones, addresses) | Privacy by design (SEC-22) |
| NG-07 | Microservices, Redis, Kafka, vector databases, separate worker deployments | Architectural constraint (ADR-001, ADR-002, ADR-003) |
| NG-08 | Writing to systems other than Google Sheets and Slack webhooks | Scope control |
| NG-09 | SSO/SAML, MFA, billing | Out of competition scope. Recorded as a risk |
| NG-10 | Natural-language chat over data ("ask anything") | Unbounded and unverifiable. ROOT uses structured, evidence-backed workflows |
| NG-11 | Mobile-native apps | Responsive web only |

## 6. Requirements

### 6.1 Functional requirements

| ID | Requirement | Spec reference |
|---|---|---|
| FR-01 | Users can register, sign in and sign out with server-side sessions | SECURITY_MODEL §4 |
| FR-02 | Users can create businesses (tenants) and manage memberships with roles `owner`, `analyst`, `viewer` | SECURITY_MODEL §5 |
| FR-03 | A dataset upload of four CSV files creates one immutable, versioned dataset | DATA_CONTRACTS §2, §6 |
| FR-04 | Uploads are validated for encoding, schema, types, ranges, uniqueness and referential integrity, with a row-level validation report | DATA_CONTRACTS §2.6 |
| FR-05 | Financial metrics are computed per period and dimension exactly as defined in DATA_CONTRACTS §3 | DATA_CONTRACTS §3 |
| FR-06 | Customers are segmented with RFM scores and risk flags | DATA_CONTRACTS §4 |
| FR-07 | Incidents are detected deterministically and ranked by estimated financial impact | ARCHITECTURE §5 |
| FR-08 | Each incident receives competing hypotheses from the hypothesis catalog, optionally prioritised by the LLM | ARCHITECTURE §6 |
| FR-09 | Evidence is gathered only through registered, parameterised evidence tools. Every evidence item is traceable to tool, parameters, revision and result hash | ARCHITECTURE §6.3 |
| FR-10 | Hypotheses are verified by deterministic criteria into `supported`, `refuted`, `inconclusive` or `needs_human_context`, with deterministic confidence | ARCHITECTURE §6.4 |
| FR-11 | The system asks clarification questions under deterministic triggers, pauses durably and resumes on the answer | ARCHITECTURE §7 |
| FR-12 | Human context answers become attributed `human_context` evidence and can change hypothesis status | ARCHITECTURE §7.3 |
| FR-13 | Human corrections create data overlays and a new analysis revision. Original evidence is never modified | ARCHITECTURE §7.4 |
| FR-14 | Humans can reject hypotheses with a reason, and the investigation adapts | ARCHITECTURE §7.5 |
| FR-15 | Humans review findings and accept or challenge them. Challenges loop at most twice | ARCHITECTURE §7.6 |
| FR-16 | Narratives cite evidence. Every number and entity name displayed is rendered from evidence by the server, never from LLM text | ARCHITECTURE §8.3 |
| FR-17 | Investigation Replay reconstructs investigation state at any event sequence without re-executing analysis or LLM calls | ARCHITECTURE §9 |
| FR-18 | Revision comparison shows incident, impact, evidence and hypothesis differences between revisions | ARCHITECTURE §9.4 |
| FR-19 | Recovery plans are generated from the strategy catalog within human constraints, with deterministic impact estimates driven by human-confirmed assumptions | ARCHITECTURE §10 |
| FR-20 | Each action can be edited (while proposed), approved (owner only, bound to payload hash) or rejected | ARCHITECTURE §10.4 |
| FR-21 | Approved actions are executed to Google Sheets (and optionally Slack) by the job worker | ARCHITECTURE §11 |
| FR-22 | Google Sheets connection requires proof of spreadsheet control and binds a spreadsheet to one business | ARCHITECTURE §11.2 |
| FR-23 | An optional Slack incoming webhook can be configured, tested and removed | ARCHITECTURE §11.5 |
| FR-24 | Action execution status is visible. Failed actions can be retried, and execution is idempotent | ARCHITECTURE §10.5 |
| FR-25 | Owners can view the audit log | SECURITY_MODEL §9 |
| FR-26 | Investigation progress is visible live through cursor-based event polling | DATA_CONTRACTS §8 |
| FR-27 | Dashboard shows metric time series, dimension breakdowns and segment distribution | DATA_CONTRACTS §7 |
| FR-28 | Owners can delete datasets and the business. Retention jobs purge expired data | SECURITY_MODEL §10 |
| FR-29 | A seeded, scenario-parameterised synthetic data generator produces demo and test datasets. It is never used at runtime to produce results | TEST_STRATEGY §6 |

### 6.2 Non-functional requirements

| ID | Requirement | Measurable target |
|---|---|---|
| NFR-01 | Reproducibility | Same `(dataset_version, overlay_version, config_version, code_version)` gives byte-identical metric outputs and identical evidence result hashes |
| NFR-02 | Performance | 100k sales lines: ingest and validate ≤ 30 s, metrics and detection ≤ 15 s. 500k lines: ingest ≤ 120 s, analysis ≤ 60 s. Non-analytic API reads p95 ≤ 300 ms at 20 concurrent users |
| NFR-03 | Graceful degradation | LLM outage: the investigation still completes in deterministic mode. Sheets or Slack outage: actions fail recoverably |
| NFR-04 | Durability | Process crash at any node or execution step: work resumes from the last checkpoint or job lease with no duplicate external side effect |
| NFR-05 | Accessibility | WCAG 2.2 AA. Zero serious or critical axe violations on key pages |
| NFR-06 | Maintainability | Strict typing (mypy `--strict`, TS `strict`), lint clean, coverage thresholds in TEST_STRATEGY §4 |
| NFR-07 | Observability | JSON structured logs with `request_id`, `business_id` and `investigation_id`. `/healthz` and `/readyz`. No secrets or PII in logs |
| NFR-08 | Demo performance | On the demo dataset: investigation start to first clarification ≤ 60 s. Full journey ≤ 6 min |
| NFR-09 | Browser support | Latest stable Chrome, Edge, Firefox and Safari. Primary layout ≥ 1280 px, usable at 375 px |
| NFR-10 | Cost control | Per-investigation LLM budget: ≤ 40 calls and ≤ 200k total tokens (configurable). Exceeding it switches to deterministic mode |

### 6.3 Security requirements

SEC-01 … SEC-25 are defined in [SECURITY_MODEL.md §3](SECURITY_MODEL.md). They are part of this plan's requirement set and appear in the traceability matrix (§10).

## 7. Judging criteria

| ID | Criterion | How ROOT demonstrates it |
|---|---|---|
| J1 | Reliability | Deterministic analytics, reproducibility hashes, durable checkpoints, idempotent actions, deterministic fallback mode, test gates |
| J2 | Practical usefulness | Real CSV inputs, finance-grade metric definitions, executable recovery tasks in Google Sheets |
| J3 | Originality | Evidence-grounded hypothesis competition, human corrections as versioned overlays, Investigation Replay with human-influence diff |
| J4 | Automation depth | Autonomous detection, investigation, evidence collection, re-planning and integration execution |
| J5 | Human + AI theme | Human input reroutes the graph, changes data revisions and bounds the plan (§1 table) |
| J6 | Demo completeness | Full journey in ≤ 6 min (DEMO_PLAN) |

## 8. Phase plan

### 8.0 Rules that apply to every phase

- Read `AGENTS.md`, this plan and the previous engineering-log entries before starting.
- Do only the work in the phase's scope. Items listed under "Out of scope" must not be started.
- Append a section to `docs/ROOT_ENGINEERING_LOG.md` using the template in that file.
- Run every test listed for the phase plus all earlier tests. Record the actual commands and results.
- A phase is complete only when **all** of its acceptance criteria hold. Otherwise record the blocker and stop.
- Commit with a descriptive message, then push without force. Verify the push with `git ls-remote`.

---

### Phase 1: Documentation and architecture

- **Objective:** Define the product, architecture, contracts, security model, test strategy, demo and agent rules before any code exists.
- **Scope:** The eight documents listed in §0 of the engineering log.
- **Out of scope:** Any code, dependencies, endpoints or scaffolding.
- **Deliverables:** `docs/ROOT_MASTER_PLAN.md`, `docs/ROOT_ENGINEERING_LOG.md`, `docs/ARCHITECTURE.md`, `docs/DATA_CONTRACTS.md`, `docs/SECURITY_MODEL.md`, `docs/TEST_STRATEGY.md`, `docs/DEMO_PLAN.md`, `AGENTS.md`.
- **Tests:** Document consistency review (§11 coverage report).
- **Acceptance criteria:**
  - AC-1.1: All eight files exist and are committed.
  - AC-1.2: Every FR, NFR and SEC appears in the §10 traceability matrix with a phase and at least one test ID.
  - AC-1.3: Every metric in DATA_CONTRACTS §3 has a formula, a denominator-zero rule and a period basis.
  - AC-1.4: Every HITL checkpoint has persistence and resume strategies (ARCHITECTURE §7.7).
  - AC-1.5: Every external action type has authorization and idempotency rules (ARCHITECTURE §10.5, §11).
  - AC-1.6: Every phase has measurable acceptance criteria.
  - AC-1.7: Every demo step maps to an API or UI capability (DEMO_PLAN §4).
- **Dependencies:** None.

---

### Phase 2: Repository foundation and quality tooling

- **Objective:** A minimal, runnable, fully gated skeleton of the two applications.
- **Scope:**
  - `backend/` managed by `uv` with `pyproject.toml` and `uv.lock`, Python 3.12+. FastAPI app factory, Pydantic Settings, JSON logging with request ID middleware, RFC 9457 error handler, `/healthz` and `/readyz` (DB ping). SQLAlchemy 2 async engine with psycopg 3, Alembic initialised with an empty baseline migration.
  - `frontend/` Next.js (App Router) with TypeScript `strict`, Tailwind CSS, shadcn/ui initialised, ESLint, Vitest and Playwright configured. One placeholder page that calls `/api/v1/healthz` through the rewrite proxy.
  - `docker-compose.yml` with **PostgreSQL only**, for local development.
  - `.github/workflows/ci.yml` with ruff, mypy `--strict`, pytest (Postgres service), `tsc --noEmit`, eslint, vitest, Playwright smoke, pip-audit, npm audit, gitleaks.
  - Security headers middleware (backend) and `headers()` config (frontend). See SECURITY_MODEL §7.
  - `.gitignore`, `.env.example` files with names only and no values.
- **Out of scope:** Auth, business logic, database tables beyond Alembic's version table.
- **Prompt:**
  > Implement Phase 2 of docs/ROOT_MASTER_PLAN.md. Follow AGENTS.md. Before choosing a dependency version, check the official docs or registry for the current stable release and pin it in the lockfiles. Add only the dependencies listed in ARCHITECTURE §13.1 that Phase 2 actually uses. Create the backend and frontend skeletons, local Postgres compose file, CI workflow and security headers. Make every gate run and pass for real. Do not add auth or domain code. Append the Phase 2 entry to docs/ROOT_ENGINEERING_LOG.md.
- **Tests:** T-20 (headers), T-39 (problem+json shape for 404/422/500), T-41 (CI gates), T-51 (health and readiness, request_id in logs).
- **Acceptance criteria:**
  - AC-2.1: `uv run pytest`, `uv run ruff check`, `uv run mypy --strict app` pass locally and in CI.
  - AC-2.2: `npm run typecheck`, `npm run lint`, `npm run test` and `npx playwright test` (smoke) pass locally and in CI.
  - AC-2.3: `/readyz` returns 503 when the DB is unreachable and 200 when reachable (tested).
  - AC-2.4: Unknown routes return `application/problem+json` without stack traces (tested).
  - AC-2.5: pip-audit, npm audit (high/critical) and gitleaks report zero findings.
  - AC-2.6: No dependency is present that Phase 2 code does not import.
- **Dependencies:** Phase 1.

---

### Phase 3: Identity, tenancy and audit

- **Objective:** Secure accounts, sessions, businesses, memberships, RBAC, RLS and an append-only audit log.
- **Scope:** Tables `users`, `sessions`, `auth_attempts`, `businesses`, `memberships`, `audit_log`, `idempotency_keys`. Endpoints in DATA_CONTRACTS §7.2 and §7.3. Argon2id hashing, session cookie, CSRF double-submit token, login throttling, permission dependency (`require_permission`), tenant context dependency that sets `app.current_business_id` per transaction, RLS policies, an app database role without `BYPASSRLS` and without ownership, and audit triggers that deny UPDATE and DELETE. Frontend: sign-in, register, business switcher, members page.
- **Out of scope:** Datasets and analytics.
- **Prompt:**
  > Implement Phase 3 of docs/ROOT_MASTER_PLAN.md following SECURITY_MODEL §4–§6 and §9 exactly. Every tenant-scoped query must go through the tenant context. Write the authorization-matrix and cross-tenant tests so they enumerate routes from the FastAPI app automatically, which forces future routes to declare a permission. Append the Phase 3 log entry.
- **Tests:** T-12, T-13, T-14, T-15, T-16, T-17, T-18, T-38, T-47, T-48.
- **Acceptance criteria:**
  - AC-3.1: The authorization matrix test covers 100% of registered routes. A route without a declared permission fails the test.
  - AC-3.2: Cross-tenant access to any tenant route returns 404 (not 403), so resource existence is not disclosed.
  - AC-3.3: With RLS on, a direct query as the app role without `app.current_business_id` returns zero tenant rows.
  - AC-3.4: `UPDATE` and `DELETE` on `audit_log` by the app role raise errors.
  - AC-3.5: The session ID rotates on login. Logout invalidates the session server-side.
  - AC-3.6: The 6th failed login within 15 min for the same account or IP returns 429. Error messages do not reveal whether an account exists.
- **Dependencies:** Phase 2.

---

### Phase 4: Data ingestion and validation

- **Objective:** Turn four CSV files into an immutable, validated dataset version with a validation report.
- **Scope:** Upload endpoint (multipart, streaming size limits), job-queue table `jobs` and the in-process worker (ARCHITECTURE §4.3), CSV parsing and validation per DATA_CONTRACTS §2, PII-column dropping, normalized tables, `datasets`, `dataset_files`, `validation_issues`, dataset deletion. Demo data generator `backend/scripts/generate_dataset.py` (FR-29). Frontend upload page and validation report.
- **Out of scope:** Metric computation.
- **Prompt:**
  > Implement Phase 4 of docs/ROOT_MASTER_PLAN.md. Implement the job queue exactly as ARCHITECTURE §4.3 describes, because later phases depend on it. Validate exactly the rules in DATA_CONTRACTS §2.6. Rules not in the contract are not allowed: amend the contract first if one is needed. The generator must be seeded and scenario-parameterised and must not be imported by runtime code. Append the Phase 4 log entry.
- **Tests:** T-09, T-10, T-11, T-42 (dataset deletion part), T-52, T-40 (upload rate limit), T-05 (generator determinism part).
- **Acceptance criteria:**
  - AC-4.1: Each rule in DATA_CONTRACTS §2.6 has at least one failing-fixture test and produces the documented issue code.
  - AC-4.2: Uploads over the size or row limits are rejected before full buffering (streaming check).
  - AC-4.3: PII-suspect columns never reach the database (tested by querying all tables).
  - AC-4.4: A dataset version, once `ready`, cannot be modified (tested at the API and DB-privilege level).
  - AC-4.5: Ingest of 100k generated sales lines completes in ≤ 30 s on CI hardware (recorded).
  - AC-4.6: The same seed produces byte-identical CSV files.
- **Dependencies:** Phase 3.

---

### Phase 5: Analytics engine and dashboard

- **Objective:** Exact metrics and segmentation, analysis revisions, metric APIs and dashboard.
- **Scope:** `analysis_configs` (seeded v1 from DATA_CONTRACTS §3–§4 constants), `analysis_revisions`, metric computation per DATA_CONTRACTS §3 (SQL NUMERIC aggregates plus pandas statistics), segmentation per §4, metric and segment endpoints, dashboard (Recharts) with accessible tables as chart alternatives.
- **Out of scope:** Incident detection.
- **Prompt:**
  > Implement Phase 5 of docs/ROOT_MASTER_PLAN.md. Implement each metric exactly as DATA_CONTRACTS §3 defines it, with golden-fixture tests computed by hand in the test file. Monetary aggregates must come from SQL NUMERIC. pandas may be used only for statistics and reshaping. Dimension names must be resolved through an allowlist. Record reproducibility hashes. Append the Phase 5 log entry.
- **Tests:** T-01, T-02, T-03, T-08 (metrics part), T-19, T-45 (analysis part), T-46 (dashboard components).
- **Acceptance criteria:**
  - AC-5.1: Every metric in DATA_CONTRACTS §3 has a golden test that matches hand-computed values exactly (Decimal equality).
  - AC-5.2: Zero denominators yield `null`, never `0` (tested for each ratio).
  - AC-5.3: Recomputing a revision gives an identical `result_hash`.
  - AC-5.4: SQL-injection payloads in dimension or filter parameters return 422 and execute no SQL (tested).
  - AC-5.5: Analysis of 100k lines finishes in ≤ 15 s (recorded).
  - AC-5.6: Every dashboard chart has an equivalent data table, and axe reports no serious or critical violations.
- **Dependencies:** Phase 4.

---

### Phase 6: Incident detection, evidence tools and verification

- **Objective:** Deterministic diagnosis core with no LLM.
- **Scope:** Detection algorithm (ARCHITECTURE §5), `incidents`, hypothesis catalog (ARCHITECTURE §6.2), evidence tool registry (§6.3), `evidence_items`, verification criteria and confidence (§6.4), `hypotheses`.
- **Out of scope:** LLM and LangGraph.
- **Prompt:**
  > Implement Phase 6 of docs/ROOT_MASTER_PLAN.md as pure, deterministic Python modules with persistence. Use the generator scenarios to prove detection on varied seeds, entity names and date offsets. Nothing in detection or verification may reference demo entity names or dates. Append the Phase 6 log entry.
- **Tests:** T-04, T-05, T-06, T-07, T-08 (evidence part).
- **Acceptance criteria:**
  - AC-6.1: Across 20 random seeds per injected scenario type, recall is ≥ 95% for the injected incident and false positives are ≤ 1 per stationary control dataset on average.
  - AC-6.2: Insufficient history (< 12 complete weeks) yields `insufficient_data` and no incidents.
  - AC-6.3: Every evidence item stores tool, validated parameters, revision ID and SHA-256 of canonical result JSON. Re-execution reproduces the hash.
  - AC-6.4: Verification output for each catalog template is covered by tests that reach every status (`supported`, `refuted`, `inconclusive`, `needs_human_context`).
  - AC-6.5: A static check (test) asserts that no runtime module imports the generator or contains demo entity literals.
- **Dependencies:** Phase 5.

---

### Phase 7: LLM gateway and LangGraph orchestration

- **Objective:** An autonomous investigation graph with durable checkpoints, a bounded LLM and a deterministic fallback.
- **Scope:** LLM gateway (one provider adapter, chosen and recorded as an amendment, with structured outputs and tool calling), versioned prompts, `llm_calls`, output validation pipeline (ARCHITECTURE §8), LangGraph state and nodes (§12), PostgreSQL checkpointer, `investigations`, `investigation_events`, investigation endpoints and event polling, live timeline UI, budget enforcement, deterministic mode. Human interrupts are created but answered only through stubs until Phase 8.
- **Out of scope:** Clarification answering UI and corrections (Phase 8).
- **Prompt:**
  > Implement Phase 7 of docs/ROOT_MASTER_PLAN.md. Choose the single LLM provider by verifying in its current official documentation that it supports structured JSON-schema output and tool calling, and record the choice as an amendment in §13 with the reason. The LLM must not output numbers or entity names (placeholder contract, DATA_CONTRACTS §9). The LLM has no tool with side effects. Implement the deterministic fallback for every LLM-backed node. Append the Phase 7 log entry.
- **Tests:** T-22, T-23, T-24, T-25 (checkpoint and resume mechanics), T-49, T-50.
- **Acceptance criteria:**
  - AC-7.1: An investigation on the generator demo dataset runs from start to the first interrupt using both the real provider and deterministic mode.
  - AC-7.2: Killing the worker mid-node and restarting resumes from the last checkpoint with no duplicated domain rows (unique constraints hold).
  - AC-7.3: The prompt-injection corpus (≥ 20 cases) causes zero schema-invalid acceptances, zero non-allowlisted tool executions and zero action creations.
  - AC-7.4: Any digit or raw entity name outside placeholders in LLM output is rejected, and the repair-or-fallback path is taken (tested).
  - AC-7.5: Budget exhaustion switches to deterministic mode and records `llm.fallback_used`.
  - AC-7.6: Each LLM call records model, prompt version, input hash, output, token usage, latency and validation outcome.
- **Dependencies:** Phase 6.

---

### Phase 8: Human-in-the-loop

- **Objective:** Human input that changes the investigation.
- **Scope:** Clarifications (create, answer, skip), human context evidence, corrections and `data_overlays` with re-revision, hypothesis rejection, findings review (accept or challenge, loop ≤ 2), resume jobs, UI for all of these, and the "Human impact" panel.
- **Out of scope:** Replay UI and the recovery plan.
- **Prompt:**
  > Implement Phase 8 of docs/ROOT_MASTER_PLAN.md exactly per ARCHITECTURE §7. Each human input must be persisted in the same transaction as its event, then resumed through a job. Prove that corrections never modify the original revision's evidence. Append the Phase 8 log entry.
- **Tests:** T-25 (full), T-26, T-27, T-28, T-46 (HITL components).
- **Acceptance criteria:**
  - AC-8.1: For each of the four interrupt types, an answer survives a backend restart between answer and resume, and the graph resumes exactly once.
  - AC-8.2: After a correction, the original revision's evidence hashes are unchanged and a new revision exists with recomputed incidents.
  - AC-8.3: A `human_context` answer changes the target hypothesis status where the catalog rule says it should (tested per template that accepts context).
  - AC-8.4: A rejected hypothesis is never presented as the root cause.
  - AC-8.5: Clarification caps (3 per incident, 6 per investigation) are enforced.
- **Dependencies:** Phase 7.

---

### Phase 9: Investigation Replay and revision comparison

- **Objective:** Make reasoning and human influence inspectable.
- **Scope:** Projection reducer (ARCHITECTURE §9), replay endpoint, revision-compare endpoint, replay UI with timeline scrubber, before/after human-influence diff, and a "re-verify" reproducibility check.
- **Out of scope:** New analysis features.
- **Prompt:**
  > Implement Phase 9 of docs/ROOT_MASTER_PLAN.md. Replay must be a pure projection over investigation_events. It must never call the LLM or re-run analytics, except for the explicit re-verify action, which compares hashes. Append the Phase 9 log entry.
- **Tests:** T-29, T-30.
- **Acceptance criteria:**
  - AC-9.1: For every seq of a completed investigation, the projection at the final seq equals the live state API response (tested).
  - AC-9.2: Replay endpoints make zero LLM calls (tested with a gateway spy).
  - AC-9.3: Revision compare lists added, removed and changed incidents and evidence with signed impact deltas.
  - AC-9.4: Re-verify reports `match` for an unmodified revision and `mismatch` when a fixture tampers with a stored result.
- **Dependencies:** Phase 8.

---

### Phase 10: Recovery planning and approval

- **Objective:** Constrained, explainable plans and a safe approval workflow.
- **Scope:** Plan-constraints interrupt, strategy catalog (ARCHITECTURE §10.2), impact estimator (§10.3), `recovery_plans`, `recovery_actions`, `approvals`, action edit, approve and reject endpoints, payload hashing, approval UI that shows the exact payload.
- **Out of scope:** External execution (Phase 11).
- **Prompt:**
  > Implement Phase 10 of docs/ROOT_MASTER_PLAN.md. Approval binds to the SHA-256 of the canonical action payload. Any edit invalidates it. Only owners can approve. Rejection is terminal and must produce no job. Assumptions are human-confirmed inputs, never LLM numbers. Append the Phase 10 log entry.
- **Tests:** T-31, T-32, T-33 (no job created part), T-46 (approval dialog).
- **Acceptance criteria:**
  - AC-10.1: Plans violating any human constraint are rejected by the validator (tested per constraint type).
  - AC-10.2: An analyst approval attempt returns 403, and an approval with a stale payload hash returns 409.
  - AC-10.3: A rejected action creates zero jobs and zero execution records.
  - AC-10.4: Impact estimates reproduce exactly from stored assumptions and evidence (golden tests).
- **Dependencies:** Phase 9.

---

### Phase 11: Integrations and action execution

- **Objective:** Exactly-once-effect execution of approved actions.
- **Scope:** `integrations` (Google Sheets service account with ownership verification; Slack webhook encrypted at rest), Sheets and Slack adapters, action executor job with pre-execution re-authorization, idempotency and reconciliation, retry and `outcome_unknown` handling, `action_executions`, integrations UI, execution status UI.
- **Out of scope:** Other integrations.
- **Prompt:**
  > Implement Phase 11 of docs/ROOT_MASTER_PLAN.md per ARCHITECTURE §10.5 and §11. Writes use valueInputOption=RAW and formula-escaping. Re-check authorization immediately before every external call. Prove exactly-once effect under duplicate approval, retry, crash and concurrent workers. Use a fake Sheets server for automated tests and a real sheet for one manual verification, recorded in the log. Append the Phase 11 log entry.
- **Tests:** T-21, T-33 (external part), T-34, T-35, T-36, T-37.
- **Acceptance criteria:**
  - AC-11.1: Under the four duplication scenarios in T-34, exactly one tab or row set exists per action.
  - AC-11.2: Cells beginning with `= + - @` (and tab or CR) are written escaped (tested).
  - AC-11.3: Spreadsheet binding without the verification code fails. The same spreadsheet cannot bind to two businesses.
  - AC-11.4: A Slack URL outside `https://hooks.slack.com/services/` is rejected, and the webhook secret never appears in any API response or log line.
  - AC-11.5: A real Google Sheet write was manually verified, with a screenshot reference in the log.
- **Dependencies:** Phase 10.

---

### Phase 12: Hardening, performance, accessibility and end-to-end

- **Objective:** Production-grade reliability and a scripted demo journey under test.
- **Scope:** Full Playwright demo journey, axe scans, performance benchmarks at 100k and 500k lines, rate limits, retention job, business deletion, security review against SECURITY_MODEL (checklist), log hygiene tests, error-state UX, cross-browser smoke.
- **Out of scope:** New features.
- **Prompt:**
  > Implement Phase 12 of docs/ROOT_MASTER_PLAN.md. Fix the defects you find. Do not weaken tests or thresholds. Run the SECURITY_MODEL §12 checklist and record each item's evidence in the log. Append the Phase 12 log entry.
- **Tests:** T-40, T-42, T-43, T-44, T-45, T-54, plus the full regression suite.
- **Acceptance criteria:**
  - AC-12.1: T-43 passes three consecutive runs with a total scripted time ≤ 6 min.
  - AC-12.2: NFR-02 targets are met and recorded.
  - AC-12.3: Zero serious or critical axe violations.
  - AC-12.4: Every SECURITY_MODEL §12 checklist item is marked pass, with test or evidence links.
- **Dependencies:** Phase 11.

---

### Phase 13: Deployment and demo readiness

- **Objective:** A live, recoverable production deployment and a rehearsed demo.
- **Scope:** Railway backend service and managed PostgreSQL (Render alternative), Vercel frontend with rewrites, environment configuration, pre-deploy migration step, backup before migration, rollback rehearsal, production smoke test, demo business seeded through the normal upload API, demo rehearsal and fallback assets.
- **Out of scope:** New features.
- **Prompt:**
  > Implement Phase 13 of docs/ROOT_MASTER_PLAN.md per ARCHITECTURE §14. Seed demo data only through the public upload API. Rehearse a rollback to the previous release and record timings. Append the Phase 13 log entry.
- **Tests:** T-53, T-43 against production (read-only-safe demo business), T-54.
- **Acceptance criteria:**
  - AC-13.1: Production smoke test passes: health, login, upload, investigation start.
  - AC-13.2: Rollback to the previous release completed and verified in ≤ 10 min (recorded).
  - AC-13.3: Secrets exist only in platform secret stores, and gitleaks on full history is clean.
  - AC-13.4: Three timed demo rehearsals are ≤ 6 min each.
- **Dependencies:** Phase 12.

---

## 9. Phase dependency graph

```
P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8 → P9 → P10 → P11 → P12 → P13
```

The chain is strictly sequential (AGENTS.md rule R-3). Phases 9 and 10 are logically independent but stay sequential to keep one reviewable change stream.

## 10. Traceability matrix

Judging columns: J1 Reliability · J2 Usefulness · J3 Originality · J4 Automation · J5 Human+AI · J6 Demo.

### 10.1 Functional

| Req | Phase | Tests | Judging |
|---|---|---|---|
| FR-01 | 3 | T-12, T-13 | J1 |
| FR-02 | 3 | T-48, T-16 | J1 |
| FR-03 | 4 | T-09, T-52 | J1, J2 |
| FR-04 | 4 | T-09, T-10 | J1, J2 |
| FR-05 | 5 | T-01, T-02, T-08 | J1, J2 |
| FR-06 | 5 | T-03 | J2 |
| FR-07 | 6 | T-04, T-05 | J1, J4 |
| FR-08 | 6, 7 | T-06, T-22 | J3, J4 |
| FR-09 | 6 | T-07, T-08 | J1, J3 |
| FR-10 | 6 | T-06 | J1, J3 |
| FR-11 | 7, 8 | T-25 | J1, J5 |
| FR-12 | 8 | T-26 | J5 |
| FR-13 | 8 | T-27 | J3, J5 |
| FR-14 | 8 | T-28 | J5 |
| FR-15 | 8 | T-28 | J5 |
| FR-16 | 7 | T-23 | J1, J3 |
| FR-17 | 9 | T-29 | J3, J5, J6 |
| FR-18 | 9 | T-30 | J3, J5 |
| FR-19 | 10 | T-31 | J2, J5 |
| FR-20 | 10 | T-32, T-46 | J1, J5 |
| FR-21 | 11 | T-35, T-34 | J2, J4 |
| FR-22 | 11 | T-36 | J1 |
| FR-23 | 11 | T-37 | J2 |
| FR-24 | 11 | T-34 | J1 |
| FR-25 | 3 | T-38 | J1 |
| FR-26 | 7 | T-49, T-46 | J4, J6 |
| FR-27 | 5 | T-46, T-44 | J2, J6 |
| FR-28 | 4, 12 | T-42 | J1 |
| FR-29 | 4, 6 | T-05 | J1, J6 |

### 10.2 Non-functional

| Req | Phase | Tests | Judging |
|---|---|---|---|
| NFR-01 | 5, 6, 9 | T-08, T-05, T-29 | J1 |
| NFR-02 | 4, 5, 12 | T-45 | J1 |
| NFR-03 | 7, 11 | T-24, T-35 | J1 |
| NFR-04 | 7, 8, 11 | T-25, T-34 | J1 |
| NFR-05 | 5, 12 | T-44 | J2 |
| NFR-06 | 2 → 13 | T-41 | J1 |
| NFR-07 | 2 | T-51 | J1 |
| NFR-08 | 12, 13 | T-43 | J6 |
| NFR-09 | 12 | T-54 | J2 |
| NFR-10 | 7 | T-50 | J1 |

### 10.3 Security

| Req | Phase | Tests |
|---|---|---|
| SEC-01 | 3 | T-12 |
| SEC-02 | 3 | T-13 |
| SEC-03 | 3 | T-14 |
| SEC-04 | 3 | T-15 |
| SEC-05 | 3 (+ every later phase) | T-16 |
| SEC-06 | 3 (+ every later phase) | T-17, T-18 |
| SEC-07 | 4 | T-09, T-10 |
| SEC-08 | 5, 6 | T-07, T-19 |
| SEC-09 | 2, 5 | T-20 |
| SEC-10 | 2 | T-20 |
| SEC-11 | 11 | T-21 |
| SEC-12 | 7 | T-22, T-23 |
| SEC-13 | 10, 11 | T-32, T-33 |
| SEC-14 | 3, 11 | T-34, T-48 |
| SEC-15 | 11 | T-37 |
| SEC-16 | 11 | T-35 |
| SEC-17 | 11 | T-36 |
| SEC-18 | 3 | T-38 |
| SEC-19 | 2 | T-39 |
| SEC-20 | 3, 4, 12 | T-15, T-40 |
| SEC-21 | 2 | T-41 |
| SEC-22 | 4, 7 | T-11, T-22 |
| SEC-23 | 4, 12 | T-42 |
| SEC-24 | 2, 11 | T-21, T-51 |
| SEC-25 | 2, 3 | T-47 |

## 11. Requirements coverage report (Phase 1 validation)

| Check | Result | Evidence |
|---|---|---|
| Every feature has an implementation phase | **Pass.** FR-01…FR-29 all mapped | §10.1 |
| Every NFR has a phase and test | **Pass.** NFR-01…NFR-10 | §10.2 |
| Every security requirement has a test strategy | **Pass.** SEC-01…SEC-25 each mapped to ≥ 1 test, and each test is defined in TEST_STRATEGY §5 | §10.3 |
| Every numerical metric has an explicit calculation definition | **Pass.** 21 metrics (M01–M21), each with formula, period basis and zero-denominator rule | DATA_CONTRACTS §3.3 |
| Every derived threshold is explicit and versioned | **Pass.** `analysis_config` v1 constants | DATA_CONTRACTS §3.5, §4 |
| Every HITL checkpoint has persistence and resume strategy | **Pass.** 4 interrupt types | ARCHITECTURE §7.7 |
| Every external action has authorization and idempotency rules | **Pass.** 4 action types | ARCHITECTURE §10.5, §11 |
| Every phase has measurable acceptance criteria | **Pass.** AC-1.1 … AC-13.4 | §8 |
| Every demo action is achievable through the architecture | **Pass.** 14 demo steps mapped | DEMO_PLAN §4 |
| Every test ID referenced exists in the test catalog | **Pass.** T-01 … T-54 | TEST_STRATEGY §5 |

Open items carried forward (not failures; owned by later phases):

- OI-1: The LLM provider is chosen in Phase 7 after verifying current structured-output and tool-calling support (ADR-016).
- OI-2: Exact dependency versions are pinned in Phase 2 against current registries (ADR-015).
- OI-3: Detection thresholds (`analysis_config` v1) are calibrated in Phase 6 against AC-6.1. Any change creates config v2 through an amendment and never edits v1.

## 12. Global deliverables per phase

Each phase delivers working code and its tests, migration files (if schema changes), updated OpenAPI-derived TypeScript types (if the API changes), an engineering-log section and a pushed commit.

## 13. Amendments

Format:

```
### AMD-<n> — <YYYY-MM-DD> — Phase <p>
- Affects: <IDs / sections>
- Change: <what>
- Reason: <why>
- Supersedes: <baseline text reference>
```

### AMD-1 — 2026-10-16 — Phase 2
- Affects: ARCHITECTURE §3, §13.1, §14.1; TEST_STRATEGY §2, §6, §7; AGENTS.md R-7; every `backend/` and `frontend/` path in all documents.
- Change: The monorepo layout is `services/api/` (FastAPI backend, Python package `app`) and `apps/web/` (Next.js frontend). Every baseline reference to `backend/` means `services/api/`, and every reference to `frontend/` means `apps/web/`. Tests live in `services/api/tests/` and `apps/web/tests/`; e2e tests live in `apps/web/e2e/`.
- Reason: The user's Phase 2 instruction specifies `apps/web` and `services/api`.
- Supersedes: ARCHITECTURE §3 module tree root names.

### AMD-2 — 2026-10-16 — Phase 2
- Affects: §8 Phase 2 and Phase 3, §10 traceability (phase column for FR-01, FR-25, SEC-01–06, SEC-18, SEC-20, SEC-25), DATA_CONTRACTS §7.2–§7.4, §7.10.
- Change: Phase 2 is re-scoped to "Application foundation, authentication and tenancy". In addition to the baseline Phase 2 scope it delivers:
  - tables `users`, `sessions`, `auth_attempts`, `businesses`, `memberships`, `datasets` (metadata only, read-only to the app) and `audit_log`
  - the app DB role with RLS
  - login, logout, me and csrf endpoints for a **seeded demonstration account** (CLI seed reading credentials from the environment)
  - CSRF, login throttling, the permission dependency, business-scoped read endpoints (`GET /businesses/{bid}`, dataset metadata list and detail, audit log)
  - the authenticated Next.js shell

  Tests T-12 (login part), T-13, T-14, T-15, T-16, T-17, T-18, T-38 and T-47 run from Phase 2. AC-3.1 to AC-3.6 are met in Phase 2 for the routes that exist, and must be re-verified in Phase 3.

  Phase 3 becomes "Account lifecycle and membership management": `POST /auth/register`, business create, update and delete endpoints, membership management endpoints and UI, the `idempotency_keys` table, T-48 and the registration part of T-12. Its prompt and acceptance criteria otherwise stand.
- Reason: The user's Phase 2 instruction requires authentication, business-scoped authorization, a minimal schema for businesses, users, dataset metadata and audit events, and an authenticated shell. Self-registration is deferred because the user asked for authentication "appropriate for a seeded demonstration account" and no more complex than needed.
- Supersedes: §8 Phase 2 "Out of scope: Auth, business logic, database tables beyond Alembic's version table" and Phase 3 scope items listed above.

### AMD-3 — 2026-10-16 — Phase 2
- Affects: SECURITY_MODEL §6, SEC-06.
- Change: RLS is `ENABLE`d, not `FORCE`d. The runtime role `root_app` is never a table owner, superuser or `BYPASSRLS`, so RLS always applies to it. `/readyz` verifies those three properties of the connected role and reports not-ready if any is violated. The owner role (migrations and admin seed CLI only) bypasses RLS as the table owner. RLS policies read `NULLIF(current_setting('app.current_business_id', true), '')::uuid`, because the setting reads as an empty string after a transaction-local value is reset. That pattern is safe across connection pooling.
- Reason: With FORCE, owner-run seeding and migrations would need policy exemptions, which adds complexity without adding protection, since the runtime never connects as the owner. The readiness check makes the guarantee enforced rather than conventional.
- Supersedes: SECURITY_MODEL §6 "`ALTER TABLE … FORCE ROW LEVEL SECURITY`".

### AMD-4 — 2026-10-16 — Phase 2
- Affects: SEC-03, SECURITY_MODEL §4.3, DATA_CONTRACTS §6.1 (`users`, `sessions`).
- Change:
  - **CSRF** uses the OWASP *signed double-submit cookie*. `GET /auth/csrf` sets an HttpOnly, SameSite=Strict nonce cookie (`__Host-root_csrf` in production) and returns `token = base64url(HMAC-SHA256(SESSION_SECRET, "csrf|" + nonce + "|" + session_binding))`. `session_binding` is the SHA-256 of the session token, or `anon` before login. A pure ASGI middleware verifies the `X-CSRF-Token` header on every unsafe `/api/` request, plus the `Origin` (or `Referer`) = `FRONTEND_ORIGIN` check. The token changes at login, so the client refetches it.
  - **`sessions`** omits `csrf_secret_hash`, `user_agent_hash` and `ip_prefix`. Nothing would use them yet.
  - **`users`** omits `failed_login_count` and `locked_until`. Throttling is computed from `auth_attempts`, a single source.
  - **`users.email`** is lowercase `text` with a `CHECK (email = lower(email))` and a unique index, instead of `citext`. This avoids an extension.
- Reason: The design is simpler and stateless, with equivalent protection, and has no columns without readers.
- Supersedes: SECURITY_MODEL §4.3 token derivation, and the DATA_CONTRACTS §6.1 column lists for `users` and `sessions`.

### AMD-5 — 2026-10-16 — Phase 2
- Affects: ARCHITECTURE §13.1, TEST_STRATEGY §4, DATA_CONTRACTS §7.1.
- Change:
  - **Toolchain verified against registries on 2026-10-16:**
    - Python 3.14.
    - Node.js 24 LTS. Node 20 reached end-of-life in April 2026, and Vitest 5 and jsdom require Node ≥ 22.
    - TypeScript 6.0. typescript-eslint supports `<6.1`, so TypeScript 7 is excluded.
    - ESLint 9. The `eslint-config-next` plugins declare a peer range of `≤ 9`.
    - PostgreSQL 18 for local development and CI.
  - **Added dev dependencies:**
    - `pytest-cov` and `@vitest/coverage-v8`, required by the coverage gates.
    - `jsdom`, `@testing-library/react` and `@vitejs/plugin-react`, for Vitest component tests.
  - **Problem `type` URIs** are `urn:root:problem:<code>`, instead of the placeholder domain used in the DATA_CONTRACTS §7.1 example.
- Reason: The prescribed versions must be current and mutually compatible, and no domain is owned.
- Supersedes: the ARCHITECTURE §13.1 version notes, and the DATA_CONTRACTS §7.1 example `type` value.
- Correction (same day, during implementation):
  - **TypeScript is 5.9.3**, not 6.0. `openapi-typescript` 7.13 declares the peer range `typescript ^5`, and forcing peer resolution is not acceptable.
  - `@vitejs/plugin-react` is **not** added: Vitest transforms TSX without it.
  - `vite` 8.3.4 **is** added, because it is a peer dependency of Vitest 5.
  - **shadcn/ui** is used through its generated components only. Their runtime dependencies are `@base-ui/react`, `class-variance-authority` and `cn`. The `shadcn` CLI and `tw-animate-css` packages are not installed: none of their CSS utilities are used, and the CLI pulled in vulnerable transitive packages.
  - Next.js `cacheComponents` and `partialPrefetching` are disabled. The per-request nonce CSP (SECURITY_MODEL §7) requires dynamic rendering, and every authenticated page is per-user.

### AMD-7 — 2026-10-16 — Phase 2
- Affects: TEST_STRATEGY §4 (npm audit gate), SEC-21.
- Change: The npm audit gate is two checks.
  - `npm audit --omit=dev --audit-level=high` must report zero findings.
  - `npm run audit:gate` fails on any high or critical advisory, except those listed with an expiry date in `apps/web/scripts/audit.mjs`.

  One exception is recorded: **GHSA-vfj7-8cjw-p6xm** (`braces`, stack-exhaustion DoS through deeply nested glob patterns). It expires 2027-01-31. No patched release exists, for any version. The package is reachable only through `eslint-config-next` → `@next/eslint-plugin-next` → `fast-glob` → `micromatch` → `braces`. That chain is development-only lint tooling and is not in the production dependency tree, which is verified by the `--omit=dev` audit. Its glob patterns come from repository configuration, never from user input, so the DoS is not attacker-reachable.
- Reason: Without a fix, a blanket `--audit-level=high` gate would either block all work or tempt a broad ignore. A narrow, dated, documented exception keeps the gate meaningful.
- Supersedes: the TEST_STRATEGY §4 row "pip-audit, npm audit --audit-level=high: 0 findings" (npm part only).

### AMD-6 — 2026-10-16 — Phase 2
- Affects: SECURITY_MODEL §10 (audit retention), SEC-18.
- Change: In Phase 2 the `audit_log` trigger rejects every UPDATE and DELETE, for all roles. The 365-day retention deletion path is designed and amended in Phase 12, when the retention sweep is implemented.
- Reason: No retention job exists yet. A dormant exemption would be speculative code.
- Supersedes: SECURITY_MODEL §10 audit-log row (mechanism only; the retention period stands).

### AMD-8 — 2026-10-16 — Phase 3
- Affects: §8 (phase numbering and Phase 3/4 scope), §10 (phase columns), DATA_CONTRACTS §2, §2.6, §6.2, §7.3, §7.4. AGENTS.md R-3 is unchanged.
- **Change 1, numbering.** From Phase 3 onward, phase numbers follow the user's instructions. User Phase *k* (k ≥ 3) is baseline Phase *k + 1*, so user Phase 3 is the baseline Phase 4 "Data ingestion and validation". Its prompt, tests (T-09, T-10, T-11, T-42 dataset part, T-52, T-40 upload part, T-05 generator part) and acceptance criteria AC-4.1 to AC-4.6 apply to it, extended below. The AMD-2 "account lifecycle and membership management" scope (self-registration, membership endpoints, T-48) moves into the baseline Phase 12 hardening phase. The one exception is `idempotency_keys`, which is delivered now because uploads require it.
- **Change 2, Phase 3 scope additions** (user instruction):
  - **Files.** The order-line file is named **`orders.csv`** (one row per order line), and its table is `order_lines`. Columns are as DATA_CONTRACTS §2.3.
  - **Column mapping with a human checkpoint.** Headers are normalised and matched to canonical columns. Exact matches are accepted automatically. Synonym and ambiguous matches are only *proposals*: the dataset waits in `awaiting_mapping` until a user with `dataset:upload` confirms them. A required column with no candidate rejects the upload with an explanation.
  - **Currency and time.** An optional `currency` column on `orders.csv`, and an optional `currency` form field, must equal the business currency (no FX, A1). A `source_timezone` form field (IANA, default the business time zone) sets how naive date-times are read. Values are stored as a calendar date in the business time zone, plus a UTC `timestamptz` when a time was supplied.
  - **Profiling summaries and provenance.** `source_row` is kept on every record, along with file SHA-256 and the mapping used.
  - **Retrieval and export.** Paginated record retrieval, and CSV export with formula-injection escaping (SEC-16).
  - **Configurable business settings.** `PATCH /businesses/{id}` (owner) changes `currency` (only while no dataset exists) and `timezone`.
  - **Duplicate uploads.** Re-uploading content identical to an existing non-failed version of the business returns 409 `duplicate_dataset`. A corrected re-upload creates version N+1, and earlier versions stay immutable.
- **Change 3, deferred.** Dataset deletion (part of T-42) moves to the baseline Phase 12 together with the retention sweep. Phase 3 needs no delete path, and versions are immutable.
- Reason: The user's Phase 3 instruction.
- Supersedes: the baseline file name `sales.csv` and table name `sales_lines`, the DATA_CONTRACTS §2.5 "header matching is case-insensitive" rule (now the mapping procedure), and the dataset status list in DATA_CONTRACTS §6.2.

### AMD-9 — 2026-10-16 — Phase 3
- Affects: ARCHITECTURE §13.1 (backend runtime dependencies), §16 (ingestion performance notes).
- Change:
  - **New dependency `python-multipart` 0.0.32.** Starlette requires it to parse `multipart/form-data`, and dataset uploads need multipart.
  - **COPY under RLS.** PostgreSQL rejects `COPY FROM` into tables with row-level security. Ingestion therefore `COPY`s into transaction-scoped temp staging tables, which have no RLS, and persists with `INSERT … SELECT`, which passes the RLS `WITH CHECK`. This keeps RLS on every tenant table without giving the app role BYPASSRLS.
  - **Benchmark job.** The ingestion benchmark (`pytest -m perf`) runs on demand in CI (`workflow_dispatch`), not on every push.
- Reason: Phase 3 implementation findings.
- Supersedes: none (additive).

### AMD-10 — 2026-10-16 — Phase 4
- Affects: §8 (Phase 4 scope), DATA_CONTRACTS §2.1, §2.3, §2.6, §3, §3.5, §4, §6.3, §7.5. ARCHITECTURE §5, §6.3, §13.1. SECURITY_MODEL §5.1.
- **Scope.** User Phase 4 is the baseline Phase 5 analytics engine plus the deterministic parts of baseline Phase 6:
  - incident detection exactly per ARCHITECTURE §5
  - severity and affected-record counts
  - source-level evidence items
  - analysis revisions

  The hypothesis catalog and verification (ARCHITECTURE §6.2, §6.4) remain with the LLM and orchestration phase. The UI is a lean, table-based analytics page; charts (Recharts) are deferred to the dashboard work, so no unused dependency is added.
- **Customer segmentation (replaces the DATA_CONTRACTS §4 segment table).** The prompt requires the lifecycle segments `loyal`, `at_risk`, `dormant` and `insufficient_history`, and forbids calling a decline confirmed churn without evidence. RFM scores (R, F, M from 1 to 5, exactly as §4) and the flags `return_risk` and `key_account` are still computed and stored. Lifecycle segment, as of T, first match wins:
  1. `dormant`: recency_days > `dormant_days` (180), **or** (lifetime_orders ≥ 3 ∧ recency_days > `dormant_gap_multiplier` (4.0) × median_gap_days ∧ recency_days > `churn_min_recency_days` (30)).
  2. `insufficient_history`: lifetime_orders < 3 (no purchase cadence can be estimated), **or** T − first_order_date < `min_history_days` (60).
  3. `at_risk`: recency_days > `churn_gap_multiplier` (2.0) × median_gap_days ∧ recency_days > 30. This is the §4 `churn_risk` rule, now a segment, so the `churn_risk` flag is removed. The output says "overdue versus own purchase cadence" and never "churned".
  4. `loyal`: everything else (three or more orders, on cadence, active within 180 days).

  `median_gap_days` is the median of the gaps between consecutive distinct order dates. A customer is never classified from orders after T, and a dataset that covers less than `dormant_days` cannot produce absolute-inactivity dormancy.
- **Cancelled order lines.** `orders.csv` gets an optional `status` column (synonyms `order_status`, `line_status`). Allowed values are `completed` (default when the column is absent or blank) and `cancelled` (`canceled` is accepted); anything else is V-016. Cancelled lines are stored, but excluded from every sales metric (L in §3.2) and from customer statistics. They are reported as the informational counts `cancelled_lines` and `cancelled_value` (Σ quantity × unit_price − discount). A return referencing a cancelled line is rejected with the new code **V-038**.
- **Missing costs.** `products.unit_cost` becomes optional, and a blank value gives a new warning, **V-045** "unit cost missing". COGS (M06, M07), gross profit (M08) and gross margin (M09) are computed for a scope only when every line and return in that scope has a known cost; otherwise they are `null`, with the restriction `missing_costs` (which lists the products concerned). A COGS figure is never estimated from other products. Recognised COGS is an estimate under A4 (constant current cost), and the output labels it that way.
- **Equal-period comparison.** For a requested inclusive range [a, b], the comparison period is [a − n, a − 1], where n = b − a + 1 days. If the comparison period starts before the dataset's first order date, it is `unavailable` (restriction `comparison_unavailable`) rather than computed on partial data. If b > T, the current period is marked `incomplete_period`. Changes are absolute, and relative is `(cur − prev)/|prev|`, which is null when prev = 0.
- **Restrictions (data-quality warnings that block unsupported calculations).** Every analytics response carries `restrictions[]` of {code, message, affects}. The codes:

  | Code | When |
  |---|---|
  | `missing_costs` | Some lines or returns in scope have no product cost |
  | `incomplete_period` | The range extends beyond T or before the data start |
  | `comparison_unavailable` | The equal comparison period is not fully covered |
  | `insufficient_history` | Fewer than 12 complete weeks, so detection is not run |
  | `immature_returns` | Orders within `return_maturity_days` of T are in scope, so refund rates can still rise |
  | `low_sample` | Segmentation n < 20 |
  | `cancelled_excluded` | Informational |

- **Detection details left open by ARCHITECTURE §5:**
  - **Metric applicability:** all 7 metrics for `overall`, `category`, `region` and `channel`. `net_revenue`, `gross_margin_pct`, `refund_rate` and `discount_rate` for `product`. `net_revenue`, `active_customers`, `aov` and `refund_rate` for `customer_segment`.
  - **Rates with a zero baseline median:** the relative-change condition counts as satisfied when Δabs exceeds its threshold (an infinite relative change).
  - **Undefined weekly values** (zero denominators, missing costs) are excluded from medians. Fewer than `min_baseline_weeks` defined values means the slice is not tested.
  - **First week:** only complete weeks count, so the first week counts only if the first order date is a Monday.
  - **Severity** from the impact share of overall `net_revenue_W`: ≥ 5% `critical`, ≥ 2.5% `high`, ≥ 1% `medium`, else `low`. When `net_revenue_W` ≤ 0, severity is `critical`.
  - **Affected records:** order lines, distinct orders and distinct customers of the slice in W; plus returns of the slice in W for refund metrics. They are reported as "records in scope", which does not claim they caused the change.
- **Evidence kinds (insert-only, SHA-256 of canonical JSON):**
  - `incident_detection`: the weekly series, statistics, thresholds and config version.
  - `source_records`: a reproducible filter specification, exact counts, and up to 25 source references (file, `source_row`, key), ordered by contribution.
  - `return_reason_mix`: refund share by reason, B versus W, for refund incidents.
- **Precision.** Money stays `Decimal` from `NUMERIC(18,4)`. Analytics runs in a local decimal context with 60 significant digits. Money is output as 4-dp strings and ratios as 6-dp strings (half-even), always computed from unrounded sums. Medians and MADs use exact `Decimal` on aggregated weekly values; z is reported as a float, rounded to 4 dp.
- **Dependencies.** pandas is **not** added. Detection works on at most a few thousand aggregated weekly values, and the standard library plus SQL aggregation is enough (ADR-005 intent kept: money in SQL NUMERIC). `hypothesis` is added as a dev dependency for property tests.
- **Permissions.** New permission `analysis:run` (owner, analyst). `analytics:read` covers owner, analyst and viewer.
- **Execution.** Analysis runs as the job `analyze_dataset`. It is enqueued automatically when ingestion makes a dataset `ready`, and can be re-requested; the same (dataset, config version, code version) is deduplicated.
- Reason: The user's Phase 4 instruction, and gaps in the baseline contract (cancellations, missing costs, segment vocabulary).
- Supersedes: the DATA_CONTRACTS §4 segment table and `churn_risk` flag; `unit_cost` required in §2.1; the §2.3 column list (adds `status`).

### AMD-11 — 2026-10-16 — Phase 4
- Affects: ARCHITECTURE §5.4 (`aov` window value), DATA_CONTRACTS §11 (default ranges), SECURITY_MODEL §6 (database privileges).
- Change:
  1. **`aov` window value.** For W and B, `aov` is computed as the aggregate ratio Σ net_sales / Σ orders, like the rate metrics, rather than as the mean of weekly AOVs. Its change thresholds remain those of an amount (15% relative). Reason: A11 forbids averaging ratios.
  2. **Default API ranges.** Analytics endpoints default to [first completed order, last activity], where last activity is the later of the last completed order date and the last return date. A range ending at T would silently omit refunds recorded after the last order. Detection and segmentation keep T = last completed order date (ARCHITECTURE §5.1).
  3. **Statistics refresh.** The owner-defined `SECURITY DEFINER` function `refresh_dataset_statistics()` runs `ANALYZE` on `products`, `customers`, `order_lines` and `returns`. Ingestion calls it in the same transaction that makes a dataset ready. The app role has only `EXECUTE`. The function takes no arguments, has a fixed `search_path`, and reads or writes no tenant data.

     Reason: this was found by measurement. Without fresh statistics, the third and later datasets in a database were planned with nested loops, and analysis took 24–27 s instead of 0.9 s. The runtime role cannot `ANALYZE` tables it does not own, and `MAINTAIN` would require PostgreSQL 17 or later.
- Supersedes: ARCHITECTURE §5.4 step 3 for `aov` only.

### AMD-12 — 2026-10-17 — Phase 5
- Affects: §8 (Phase 5 scope), ARCHITECTURE §4.4, §6.2–§6.4, §8, §12, §13.1, DATA_CONTRACTS §6.3, §7.6, §8, §9, SECURITY_MODEL §5.1, TH-08.
- **Scope.** User Phase 5 is the rest of baseline Phase 6 (hypothesis catalog, evidence tool registry, deterministic verification) plus baseline Phase 7 (LLM gateway, LangGraph graph, PostgreSQL checkpointer, investigations, events, `llm_calls`, endpoints, budgets, deterministic mode), **without human interrupts**. Clarification answering, corrections, findings review and the `interrupt()` nodes stay in the human-in-the-loop phase. The "missing-context detection" node records open context requests (rule-generated questions) in the output; it does not pause the graph.
- **Provider.** Anthropic, through the official Python SDK `anthropic` 1.15.0, model `claude-opus-5-5` (configurable). Verified in the current official SDK documentation: JSON-schema structured output (`output_config.format`) and tool use with `strict` schemas. The API keeps no conversation state between requests. Settings: `LLM_PROVIDER` = `disabled` (default; deterministic mode) or `anthropic`, which requires `ANTHROPIC_API_KEY`. CI never calls a real provider: tests inject a scripted fake provider. One opt-in live test runs only when `ROOT_LIVE_LLM_TESTS=1` and a key are present.
- **Tool selection through structured output.** The LLM requests evidence tools by returning a JSON object whose schema is generated from the tool parameter models: an `anyOf` over the allowlisted tools, each with a `const` name and its own argument object. This replaces provider-native tool calling for `request_evidence` (DATA_CONTRACTS §9.2). Reason: Opus 5.5 rejects forced `tool_choice`, so a guaranteed, schema-valid response is available only through structured output. One validation path covers the real provider and the CI fake alike. The LLM never executes anything. The runtime validates each request with the tool's strict Pydantic model (extra fields forbidden, enums for every identifier), then runs an allowlisted read-only function. The provider schema omits keywords the API does not support (`minLength`, `maximum`, …), and Pydantic enforces them after the response.
- **Graph (Phase 5).** `initialize → select_incident → generate_hypotheses → select_tools → retrieve_evidence (⟲ select_tools, at most 2 rounds) → verify_evidence → detect_missing_context → select_incident … → synthesize_output → finalize`. `initialize` routes straight to `finalize` for `insufficient_data` or when there are no incidents (`completed_no_incidents`).
  - The investigation binds to the dataset's finished Phase 4 analysis revision.
  - POST returns 409 `analysis_not_ready` until that revision exists.
  - State holds IDs, counters and validated tool requests (enums only).
  - `thread_id` = investigation ID. `durability="sync"`, so a checkpoint is written after every node.
  - A failed job resumes from the last checkpoint.
  - `recursion_limit` is a hard stop: hitting it fails the investigation with `workflow_limit`.
- **Hypothesis catalog v1 (`catalog_version` 1.0.0).**
  - Templates implemented: `H_RETURNS_QUALITY`, `H_FULFILLMENT`, `H_PRICE_DISCOUNT`, `H_SEGMENT_SHIFT`, `H_CONCENTRATION`, `H_CHANNEL`, `H_DATA_QUALITY`, `H_EXTERNAL_CONTEXT`.
  - `H_CUSTOMER_CHURN` is renamed `H_SEGMENT_SHIFT` ("fewer purchases from previously established customers"), so that no output asserts churn (AMD-10).
  - `H_MIX_SHIFT` is deferred until a mix-rate decomposition tool exists. It is not stubbed.
  - Criteria, weights and thresholds are in `app/investigation/catalog.py` and DATA_CONTRACTS §12. Every template except `H_CHANNEL` carries one human criterion; in Phase 5 it is always unanswered.
  - **Verification change.** A data criterion whose evidence is missing evaluates to *unknown*. A hypothesis with an unknown data criterion cannot be refuted by low confidence; it is `inconclusive`, and the gap is reported. ARCHITECTURE §6.4 would otherwise refute a hypothesis for lack of data.
- **Evidence tools (all read-only, scoped by the runtime to the investigation's business, dataset, revision and incident slice).**
  - `metric_breakdown` (business metric retrieval and contribution).
  - `period_comparison` (equal-period, AMD-10).
  - `return_investigation` (reason mix, excess-refund contributors, matured cohort refund rates).
  - `segment_analysis` (lifecycle segments as of the baseline end).
  - `customer_exposure` (customers in scope, top-k share, repeat buyers).
  - `evidence_lookup` (stored evidence of the same investigation and incident).
  - `data_quality_check` (zero-order days, weekly volume anomalies, near-duplicates, cancellations, missing costs, immature returns).

  These replace the baseline §6.3 tool list. Arguments are enums and small integers only. The LLM never supplies a business, dataset, revision, entity value or SQL fragment.

  Results are stored as `evidence_items` (new `kind` values, `params_hash`, `query_version`, `requested_by`). They are unique per (revision, kind, params_hash), so an immutable revision's tool result is computed once and reused across investigations (the calculation cache). `investigation_evidence` links items to investigations.
- **Facts never depend on the LLM.**
  - Verdicts use only the catalog-required tool calls, with fixed parameters. The system always runs those calls, whatever the LLM selects. LLM-requested extra evidence can be cited in the narrative but cannot change a status or confidence.
  - The root-cause candidate is chosen by confidence and catalog order. LLM priority orders the presentation only.
  - Observations (source facts) are rendered from evidence by code templates. They are stored separately from hypotheses and from the LLM narrative.
- **Evidence Critic (deterministic, `app/investigation/critic.py`).**
  1. **Tool output sanity.** Shares in [0, 1], counts are non-negative integers, decompositions sum to their totals within 0.0001, rates are finite. An impossible result is rejected and never stored.
  2. **Integrity.** Each evidence hash is recomputed before verification. A mismatch excludes the item.
  3. **LLM output checks**, in addition to the §8.3 pipeline:
     - Every citation and placeholder must reference evidence linked to *this* investigation and incident, and a field that exists and is numeric.
     - Root causes must be `supported` hypotheses.
     - A sentence must not state a direction ("rose", "fell", …) that contradicts the sign of the signed field it cites.

  Rejection triggers one repair attempt, then the deterministic fallback.
- **LLM inputs.** Aggregates, enum codes, evidence IDs and opaque entity references (`{{entity:<kind>:<hash>}}`) only. Dimension values, product names and IDs, customer IDs and `reason_text` are business text and are never sent, so an injection planted in a CSV field cannot reach the model. `untrusted_text` is therefore always empty in Phase 5.
- **Limits (configurable, persisted, enforced in code).**
  - **LLM budget:** ≤ 40 calls and ≤ 200 000 tokens per investigation. A per-call timeout of 60 s.
  - **Retries:** 2 retries for timeouts, 429 and 5xx, with backoff that honours `retry-after`, capped at 10 s.
  - **Deadline:** LLM use stops after 300 s of wall-clock time per investigation.
  - **Circuit breaker:** the first provider failure that survives retries switches the investigation to deterministic mode.
  - **Tools:** ≤ 6 LLM-requested tool calls per incident and ≤ 40 evidence items per incident. Duplicate requests are collapsed.
  - **Workflow:** at most 2 selection rounds per incident, and `recursion_limit` 120.
  - **Starts:** 20 investigation starts per business per hour (SEC-20).
- **Schema (migration `0004`).**
  - New tables `investigations`, `investigation_events`, `hypotheses`, `hypothesis_evaluations`, `investigation_evidence`, `clarifications` (status `open` only in Phase 5), `findings` (one round, no review status yet) and `llm_calls`, all with RLS.
  - `evidence_items` extended as above.
  - New job type `run_investigation`.
  - LangGraph checkpoint tables in schema `langgraph`. The DDL is the library's own migration list, frozen into the Alembic migration and recorded in `checkpoint_migrations` so the library never runs DDL at runtime. A test fails if the installed library expects more migrations. The app role gets `USAGE`, `SELECT`, `INSERT` and `UPDATE` there, but not `DELETE`.
- **Permissions.** `investigation:read` (owner, analyst, viewer) and `investigation:run` (owner, analyst), per SECURITY_MODEL §5.1. `investigation:cancel` is deferred with the cancel endpoint.
- **Dependencies.** `anthropic` 1.15.0, `langgraph` 1.2.15 and `langgraph-checkpoint-postgres` 3.2.0, verified on PyPI on 2026-10-17. `langgraph` pulls in `langsmith`. Tracing stays off: startup refuses `LANGSMITH_TRACING`/`LANGCHAIN_TRACING_V2` set to true, so no investigation data can leave through tracing.
- Reason: The user's Phase 5 instruction (typed read-only tools, an Evidence Critic, injection resistance, bounded execution, persistent checkpoints, mocked-LLM CI), Opus 5.5 API constraints, and gaps in the baseline catalog.
- Supersedes: ARCHITECTURE §6.3 tool list, the §6.2 rows `H_CUSTOMER_CHURN` (renamed) and `H_MIX_SHIFT` (deferred), the §6.4 low-confidence refutation when evidence is missing, the §12.2 graph for Phase 5 (no interrupts), and DATA_CONTRACTS §9.2 `request_evidence` (provider tool calling).

### AMD-13 — 2026-10-17 — Phase 5 completion audit
- Affects: AMD-12; baseline AC-6.4, AC-7.1–7.6; ARCHITECTURE §8, §12; DATA_CONTRACTS §12; TEST_STRATEGY §4–5.
- **Scope and acceptance.** The user's renewed Phase 5 instruction explicitly authorizes finishing the interrupted Phase 5 work. It is backend-only: the investigation API returns structured observations, hypotheses, verdicts, gaps and rendered findings. Human answers, interrupts, recovery actions, replay UI and live timeline UI remain outside this phase. Baseline AC-7.1 is replaced for this phase by completion from real uploaded data in deterministic and scripted-provider modes, plus a separate opt-in real-provider integration test. A live call is not a default CI/completion requirement; its actual execution or absence must be logged. AC-7.2–7.6 remain applicable. AC-6.4 requires every *reachable* status for each template (a data-only template cannot require human context); the human-answer transitions remain in the human-in-the-loop phase. Detection recall/false-positive thresholds and all coverage thresholds remain unchanged.
- **Execution limits.** Before implementation, add a whole-run `investigation_timeout_seconds` limit (default 600 seconds, configurable) around checkpoint loading and graph execution, independent of the 300-second LLM deadline. Expiry records a recoverable `failed` investigation with `workflow_timeout`. A PostgreSQL session advisory lock keyed by investigation ID on the dedicated checkpoint connection serializes overlapping workers, including lease recovery; process/connection termination releases the lock. Bound the lock wait by the same run timeout.
- **Budget accounting.** Reserve input tokens conservatively from UTF-8 request/schema bytes plus framing overhead, and cap each requested output to the remaining token allowance. Failed attempts without provider usage consume their full reservation; successful attempts record provider-reported usage. Do not retry through an exhausted budget. Report this conservative accounting in the tool/LLM contract.
- **Audit process.** Correct earlier completion overstatements by appending an explicit reconciliation in the Phase 5 log, never editing earlier test evidence. Prior CI, production performance and unimplemented generator scenarios remain unverified unless executed in this session. The renewed instruction authorizes Phase 5 completion work, not a claim that unobserved historical gates passed.
- Reason: User asks to audit Claude's incomplete work, finish Phase 5, use parallel coding agents, run rigorous failure tests, and keep Phase 6 out of scope. The completion audit found missing total runtime/concurrency bounds and an interrupt-based acceptance criterion incompatible with AMD-12.
- Supersedes: baseline AC-7.1 and the Phase 5 applicability of baseline live timeline/interrupt scope only; clarifies AC-6.4. Adds execution bounds without lowering existing quality, security or detection thresholds.

### AMD-14 — 2026-10-17 — Phase 5 contract reconciliation
- Affects: DATA_CONTRACTS §7.1, §12.4, §12.6; AMD-8 phase-number mapping after Phase 5.
- **Start idempotency.** Investigation starts reuse the existing critical-POST key storage, including its 24-hour TTL and 409 on body/operation mismatch. The header format is the established upload format: 8–100 characters matching `[A-Za-z0-9_-]`, including UUID hex keys. Replays return the original 202 body even after the investigation completes. Expired keys can be renewed atomically; concurrent cross-operation conflicts roll back rather than saving untracked work. This extends the §10.1 format to Phase 5 starts instead of introducing a second key format.
- **Interpretation labels.** Findings include a label distinguishing AI interpretation from the deterministic source observations. The Critic enforces referential, numeric, status and directional checks; arbitrary prose is interpretation, not independently proved semantic entailment. Source observations, quantitative values, verdicts and root-cause candidate ranking remain deterministic.
- **Remaining numbering.** Because user Phase 5 absorbs baseline Phases 6 and 7, subsequent user phases map as follows: Phase 6 → baseline 8 (human-in-the-loop), Phase 7 → baseline 9 (replay), Phase 8 → baseline 10 (recovery approval), Phase 9 → baseline 11 (integrations), Phase 10 → baseline 12 (hardening and deferred account lifecycle), Phase 11 → baseline 13 (deployment). All baseline acceptance IDs retain their original numbering. No later phase is authorized by this mapping alone.
- Reason: Complete the missing Phase 5 API contract, preserve durable retry semantics, distinguish evidence from model prose, and remove ambiguity left by the merged phases.
- Supersedes: DATA_CONTRACTS §7.1 UUID-only key format for investigation starts, and AMD-8's offset mapping only for user phases after 5.

### AMD-15 — 2026-10-17 — Phase 6
- Affects: §8 (user Phase 6 scope), ARCHITECTURE §4.4, §6.2–§6.4, §7.1–§7.3, §7.7, §9.3, §12; DATA_CONTRACTS §6.3, §7.6, §7.7, §8, §12.3; SECURITY_MODEL §5.1; AMD-12 (graph, catalog, LLM deadline); AMD-14 (phase mapping).
- **Scope.** User Phase 6 delivers the baseline Phase 8 *clarification* interrupt end to end (missing-context detection, one question at a time, durable `interrupt()`, answer API, human-context evidence, re-evaluation, resume jobs, caps, UI) plus, from baseline Phase 9, the before/after comparison of interpretation versions ("Investigation Replay" as requested by the user). Deferred to user Phase 7 together with the rest of baseline Phase 9: data corrections and overlays (`exclude_records`, `reclassify_return_reason`, `exclude_baseline_period`), the hypothesis-rejection and findings-review/challenge endpoints, the skip endpoint (a "Not sure" option exists instead), event-sequence projection (`?at_seq=`), analysis-revision compare and re-verify. Recovery plans and actions stay in later phases; Phase 6 "recommendations" are deterministic recommended next checks attached to findings, never executable actions.
- **Graph 2.0.0.** `initialize → (select_incident → generate_hypotheses → select_tools → retrieve_evidence ⟲ → verify_evidence)* → draft_findings → assess_context ─(question)→ await_clarification [interrupt] → apply_answer → (select_incident … for affected incidents) → draft_findings → assess_context … ─(none, or caps reached)→ finalize`. All incidents are investigated before the first question, so version 1 is the complete AI interpretation before any human input. `await_clarification` only reads before `interrupt()`; the resume value is a strict model `{clarification_id, human_input_id}` (the answer itself is read from the database). On revisits after an answer, only catalog-required tools of newly added hypotheses run (no extra LLM tool selection), so per-incident tool limits hold. Investigations created by graph 1.0.0 that are still queued or running are not resumed by the new graph and fail with `graph_version_unsupported` (they can be started again); finished ones remain readable.
- **Statuses and durability.** New investigation status `awaiting_human` (not terminal, not leased). After `ainvoke` returns with a pending interrupt the runtime stores the LangGraph `checkpoint_id` and interrupt ID on the clarification (`awaiting`) and marks the investigation `awaiting_human` in one transaction; a lost job re-runs this idempotently. An answer is accepted only when the clarification is `awaiting`, the investigation is `awaiting_human` and the request's `checkpoint_id` equals the stored one (otherwise 409 `stale_checkpoint`); in one transaction it inserts `human_inputs` (unique per clarification), sets the clarification `answered`, re-queues the investigation and enqueues `run_investigation` with dedupe key `resume:<clarification_id>`. The worker resumes only if the latest checkpoint still equals the stored one (else `stale_checkpoint`). `apply_answer` sets `applied` in the same transaction as its effects. Clarification statuses: `open` → `awaiting` → `answered` → `applied`.
- **Catalog 1.1.0.**
  - Every human criterion's question is `single_choice` with options that carry an effect: `confirm` (criterion satisfied), `deny` (not satisfied), `intended` (satisfied, and the mechanism was a deliberate business decision) or `unsure` (unanswered). Options may name follow-up templates to investigate.
  - `H_PRICE_DISCOUNT.C3` asks whether the higher discounting was planned: `planned_campaign` (intended, follow-ups `H_RETURNS_QUALITY`, `H_COST_PRICE`), `unplanned` (confirm), `no_known_change` (deny), `unsure`.
  - New template `H_COST_PRICE` (metric `gross_margin_pct`; follow-up elsewhere): selling prices fell relative to product cost, or the mix moved to lower-margin products. Required tool: new read-only `margin_bridge` (query version 1), an exact sequential decomposition of the gross-margin change into discount, refund and price/cost (incl. mix and restocking) effects that sum to the total change. Criteria: price/cost effect ≤ −2 pp (0.6); price/cost effect ≥ 50% of the adverse effects (0.4); human: list-price or range change (0.3, `lowered_prices` is `intended`); refuted when the price/cost effect is above −0.5 pp. Under A4 (one cost per product) a supplier-cost increase is invisible in the data; it is captured only as human context (`H_EXTERNAL_CONTEXT` option `supplier_change`).
  - Templates added as follow-ups are stored with `source = human_context`. Option sets, effects and follow-ups are catalog data, never derived from free text.
- **Verification v2 (ARCHITECTURE §6.4 completed).** human_confidence = satisfied human weight / human weight when any human criterion is answered (else null). Mixed templates: confidence = min(1, data_confidence + 0.5 × satisfied human weight); an unanswered or "unsure" human criterion keeps `needs_human_context` in [0.3, 0.7). Human-only templates: confirm → `supported` (confidence = human_confidence), deny → `refuted`, unanswered → `needs_human_context`. A data refutation always wins. A human statement contradicting the data (deny while data_confidence ≥ 0.7, or confirm while a data refutation holds) is recorded as a `conflict`; statuses and measurements stay data-driven and the findings report the conflict. `intended` sets the evaluation's `interpretation` (`unintended` for a confirmed intent question); a supported hypothesis interpreted as intended is reported as *explained by business context* and is never the root-cause candidate. Evaluations are insert-only history keyed by `(hypothesis_id, inputs_hash)` (evidence hashes, answers, catalog version); the latest one is current.
- **Missing-context detection.** Triggers CT-1…CT-4 as before plus **CT-5 intent**: the root-cause candidate has an unanswered human criterion offering an `intended` option (the data can show the mechanism but not whether it was deliberate). Priority per incident CT-5, CT-2, CT-1, CT-3, CT-4; incidents by rank. One clarification exists at a time. A criterion is asked once per incident slice; its answer applies to every investigated incident of the investigation with the same slice (dimension and value) that has that template. Caps: 3 per incident and 6 per investigation (`clarification.cap_reached`), then the investigation completes with current statuses.
- **Human input safety.** Answers need `hitl:answer` (owner, analyst; SECURITY_MODEL §5.1), membership of the business, and a clarification of that investigation. Free text (≤ 1,000 characters, no control characters other than tab and newline) is stored, attributed and displayed as plain text only: it is never parsed, never passed through the placeholder renderer and never sent to the LLM. Human context reaches the model only as catalog codes (criterion, option, effect). No human input is an authorization of anything; no new external effect exists. Every answer is attributed (`answered_by`, `answered_at`, events carry `actor_user_id`) and audited (`hitl.answered`).
- **Versions and comparison.** `draft_findings` writes findings round *n* and an insert-only `investigation_versions` row *n* (version 1 before human input; version *k*+1 after the *k*-th answer) holding a canonical snapshot (per incident: measurements = impact and evidence result hashes; hypotheses with status, confidences, interpretation and conflicts; root cause; explained-by-context; recommendations; human context; findings text) and its SHA-256. `GET …/versions/compare` is a pure diff of two stored snapshots (zero LLM calls, zero analytics queries) listing changed findings, hypotheses and recommendations and whether every earlier measurement is preserved.
- **LLM.** The synthesis prompt becomes `synthesize_findings.v2` (explains `interpretation`); the output schema is unchanged and the Critic rejects intended hypotheses as root causes. The 300-second LLM deadline is measured per active run segment (`run_started_at`), so time spent waiting for a human does not disable the model; call and token budgets remain cumulative per investigation.
- Reason: The user's Phase 6 instruction (critical scenario, durable interrupts, checkpoint checks, versions and replay), and the baseline gap that no trigger fires when the leading explanation is supported but its intent is unknown.
- Supersedes: AMD-12 graph and catalog 1.0.0 for new investigations, AMD-12 "status open only" clarifications, the AMD-12 per-investigation LLM wall-clock deadline (now per run segment), ARCHITECTURE §6.4 human-confidence and status precedence where they differ, and AMD-14's Phase 7 mapping (Phase 7 additionally owns the deferred items above).

### AMD-16 — 2026-10-17 — Groq as an alternative LLM provider
- Affects: AMD-12 (provider boundary, configuration), ARCHITECTURE LLM gateway, SECURITY_MODEL (data sent to a model provider), `.env.example`.
- **Change.** `LLM_PROVIDER` accepts `groq` in addition to `disabled` and `anthropic`. `GROQ_API_KEY` is required when it is set; `GROQ_MODEL` defaults to `openai/gpt-oss-120b`. `LLM_EFFORT` maps to Groq's `reasoning_effort`.
- **Adapter.** `GroqProvider` implements the existing `LlmProvider` protocol over Groq's OpenAI-compatible Chat Completions endpoint (`https://api.groq.com/openai/v1`), using the pinned `httpx` client.
  - Requests carry the same provider-safe JSON schema as `response_format: json_schema` in best-effort mode (`strict: false`), because strict mode requires every property to be required. The gateway still validates every response against the full Pydantic model and the Critic, with the same single repair attempt and deterministic fallback.
  - No tools, no client retries (the gateway owns retries and budgets) and no telemetry.
  - HTTP failures map to the same typed `ProviderError` kinds; `finish_reason = length` maps to `truncated`, and a refusal maps to `refused`.
- **Unchanged.** The gateway, Critic, prompts, budgets, the envelope content (codes, IDs and validated facts only; no imported text, free text or business names) and the deterministic analytical facts. A different model can change wording and prioritization only.
- Reason: the user asked to run investigations on Groq with their own key.
- Supersedes: AMD-12's "Anthropic is the only provider adapter" statement. Anthropic remains supported and is selected the same way.

### AMD-17 — 2026-10-17 — Phase 7: recovery plans and a durable approval checkpoint
- Affects: §8 (user phase mapping, AMD-14/AMD-15), baseline Phase 10, ARCHITECTURE §7.1, §10.1–§10.4, §12; DATA_CONTRACTS §6.4, §7.7–§7.8, §8; SECURITY_MODEL §5.1, SEC-13; TEST_STRATEGY T-31–T-33, T-46.
- **Scope and phase mapping.** The user's Phase 7 instruction delivers baseline Phase 10 (recovery planning and approval), not baseline Phase 9. User phases now map: Phase 7 → baseline 10; Phase 8 → baseline 11 (integrations and execution); Phase 9 → baseline 12; Phase 10 → baseline 13. The remaining baseline Phase 9 items listed in AMD-15 (corrections and overlays, hypothesis rejection, findings review, skip, `?at_seq=` projection, revision compare, re-verify) are unscheduled until a user instruction assigns them. Nothing in Phase 7 executes, schedules or stubs an external action: there is no executor, no integration and no `action_executions` table.
- **Plan-level approval (supersedes the per-action approval of ARCHITECTURE §10.4 for this phase).** A recovery plan holds competing strategies; the human selects strategies, edits constraints, assumptions, targets and permitted action types, and approves, rejects or requests changes to one exact *plan version*. Each version is immutable canonical JSON with a SHA-256 `content_hash`. A decision names `version`, `content_hash` and the paused `checkpoint_id`. Any material edit creates a new version, so an earlier approval never covers changed content.
- **Workflow.** A separate LangGraph graph `recovery-graph-1.0.0`, thread `recovery:<plan_id>`, uses the same PostgreSQL checkpointer, advisory lock, job queue and recovery rules as investigations (`run_recovery_plan` jobs). Nodes: `draft_plan` (version 1: economics evidence, strategies, estimates, optional LLM rationale, validation; skipped when a version exists) → `await_decision` (`interrupt()`; the runtime records the checkpoint on the version and sets the plan `awaiting_decision`) → `apply_decision` → (`request_changes`: version *n*+1 recalculated and re-validated → `await_decision`) | (`approve` / `reject` → `finalize`). The resume value is `{decision_id}` only; the node reloads the decision and re-checks plan, version, hash, checkpoint and, for approvals, that the decider is *still* an owner. A decision that fails these checks is recorded as `refused` and the plan waits again.
- **Statuses.** Plan: `queued → running → awaiting_decision → (queued → running →)* approved | rejected`, plus `failed`. `approved → queued` only through a revision. Decisions: `approve`, `reject`, `request_changes`, `revise` (an edit of an approved plan, which invalidates the approval: `approved_version` is cleared and the new version needs a new approval). At most one approve, reject or request_changes decision per version, and one revision per version (unique indexes), so duplicates and concurrent submissions have one effect.
- **Strategy catalog `recovery-1.0.0` (DATA_CONTRACTS §14).** Strategies apply to hypotheses that are `supported` and not explained by business context, in investigated incidents: `S_QUALITY_REVIEW`, `S_QUALITY_HOLD`, `S_GOODWILL_OUTREACH` (H_RETURNS_QUALITY); `S_DISCOUNT_GUARDRAIL`, `S_DISCOUNT_REVIEW` (H_PRICE_DISCOUNT); `S_FULFILLMENT_ESCALATION` (H_FULFILLMENT); `S_TARGETED_OFFER`, `S_CUSTOMER_OUTREACH` (H_SEGMENT_SHIFT, H_CONCENTRATION); `S_PRICE_REVIEW` (H_COST_PRICE); `S_CHANNEL_REVIEW` (H_CHANNEL); `S_DATA_FIX` (H_DATA_QUALITY, no estimate). Each declares action types, required assumptions with bounds and defaults, operational requirements, trade-offs and limitations. Action types: `review_task` (internal, allowed by default) and the risky, externally consequential `sales_hold`, `discount_policy`, `customer_contact`, `customer_offer`, which are opt-in: they must be explicitly added to `allowed_action_types`. Unknown action types are rejected (422).
- **Deterministic estimator (`app/recovery/estimator.py`).** Observed inputs come only from stored, hashed evidence: the incident's detection `impact` and window length, and a new read-only tool `slice_economics` (query version 1) for the incident slice in the current window (net revenue, net sales, gross sales, discount rate in both windows, gross profit, orders, customer-weeks; gross profit null when costs are incomplete). Formulas are listed in DATA_CONTRACTS §14.3; low/mid/high scenarios come from assumptions only. Money assumptions (`review_cost`, `contact_cost`) have no defaults: they must be provided by a person. Rate assumptions have labelled defaults. Every estimate is labelled `estimate` and separated from `observed` values, which cite `evidence_id` and field. Break-even values are computed where defined; a zero or negative contribution margin is handled explicitly and never divides by zero. Decimal arithmetic in the analytics context, money rounded half-even to cents only for output.
- **Validation before approval.** Every version stores `valid` and `validation {errors, warnings}`. Errors: `no_strategy_selected`, `unknown_strategy`, `strategy_ineligible`, `action_type_not_allowed`, `missing_assumption`, `budget_missing` (a selected strategy has a cost and no budget is set), `budget_exceeded` (Σ selected high-scenario cost > budget), `discount_exceeds_max`, `invalid_customer_targeting` (targets not among the slice's eligible customers or in an excluded region or acquisition channel), `no_targets`. Warnings: `overlapping_scope`, `non_positive_contribution_margin`. Only a valid version can be approved (409 `plan_invalid`).
- **LLM.** Optional task `explain_strategies` v1 writes rationale text per strategy within the placeholder contract (no digits or names outside evidence placeholders, citations limited to the strategy's evidence). It cannot add, remove, select, rank or parameterise strategies, cannot produce numbers, and has no decision fields; invalid output falls back to deterministic text. Rationale is presentation only; estimates are computed before and independently of it. Revisions reuse the earlier rationale and make no LLM call.
- **Authorization.** New permissions `plan:create`, `plan:decide` (reject, request changes, revise; owner and analyst) and `plan:approve` (owner only), enforced server-side per decision kind. Plans, versions and decisions carry `business_id` with RLS; cross-tenant IDs return 404. Decisions are insert-only except `applied_at`/`outcome` set once (trigger). Requests forbid extra fields, so a client cannot supply approver, role, hash-of-record or status. An optional `Idempotency-Key` replays the stored 202. Clarification answers and LLM output have no path to a decision row. Approval only records an authorization; it enqueues nothing but the internal workflow resume and changes nothing outside the database.
- **Persistence (migration `0006`).** `recovery_plans`, `recovery_plan_versions`, `plan_decisions` (replacing the baseline `recovery_actions`/`approvals` names for this phase); evidence kind `slice_economics`; job type `run_recovery_plan`. A completed investigation is immutable (migration `0004` trigger), so plan activity is not appended to its event log: the plan's history is its immutable versions, the append-only decisions and the audit log (`plan.created`, `plan.approved`, `plan.rejected`, `plan.changes_requested`, `plan.revised`). Model calls for a plan are recorded in `llm_calls` under the investigation and share its call and token budget.
- Reason: the user's Phase 7 instruction (plan versions, Approve/Reject/Request Changes, invalidation on material change, durable approval checkpoint, opt-in risky actions) and the absence of an executor in this phase.
- Supersedes: AMD-14 and AMD-15 phase mappings from Phase 7 on; ARCHITECTURE §10.2 strategy list and §10.3 formulas for this phase; §10.4 per-action endpoints; DATA_CONTRACTS §6.4 `recovery_actions`/`approvals` and §7.8 for this phase.

### AMD-18 — 2026-10-17 — Phase 8: executing approved plans in Google Sheets

- Affects: §8 baseline Phase 11, ARCHITECTURE §10.5, §11, §13.1; DATA_CONTRACTS §6.4, §7.8, §7.9; SECURITY_MODEL §5.1, SEC-13, SEC-15; TEST_STRATEGY T-21, T-33–T-36.
- **Scope.** User Phase 8 delivers baseline Phase 11 for Google Sheets only: binding a business spreadsheet with an ownership proof, executing an approved recovery-plan version as one external action, verifying it by read-back, retries, reconciliation, manual review and execution history. Slack (FR-23, T-37, the encrypted webhook) is optional in the user brief and is **not** delivered. Nothing contacts customers: a "customer-service recovery queue" is rows in the business's own spreadsheet for its staff.
- **One action per approved version.** Execution is requested explicitly by an owner (`action:execute`); approval alone still schedules nothing. The action `sheets.write_recovery_tasks` has a stable ID `uuid5(ACTION_NAMESPACE, "<plan_id>:<version>:<content_hash>:sheets.write_recovery_tasks")`, so repeated clicks, retries of the request and workflow resumes all name the same row (primary key) and the same job (dedupe key). Its payload is built by the server from the approved, hashed version content alone (never from LLM text, the request body or the client): one row in tab `ROOT Review Tasks` per selected strategy with action type `review_task`, and one row in tab `ROOT Service Queue` per targeted customer of each selected strategy with action type `customer_contact`. Other action types (`sales_hold`, `discount_policy`, `customer_offer`) are listed as `not_executed` in the payload and never performed. At most 5,000 rows. `payload_hash = sha256(canonical payload)`; `idempotency_key = sha256(action_id ":" payload_hash)`; every row starts with a key `<action_id hex>-<n>` in column A (the action ID already binds the plan, version and content hash, and the payload is a pure function of that content).
- **Destination allowlist.** The only possible destination is the business's single `verified` Google Sheets integration, captured into the action when it is created; the tab names and header layouts are server constants. No agent, model, plan or request field can name a spreadsheet, range or URL. Outbound calls go only to `https://oauth2.googleapis.com/token` and `https://sheets.googleapis.com/v4/spreadsheets/<validated id>` (SEC-15).
- **Statuses.** Action: `pending` (intent persisted, not finished) → `succeeded` (rows confirmed written) → `verified` (independent read-back matched every row exactly once); `failed` (nothing was written: pre-execution check failed, permission, destination, credentials, or retries exhausted before any write was sent); `manual_review` (an outcome that cannot be closed safely: partial write, read-back mismatch or duplicates, or retries exhausted after a write may have been sent). Each attempt is an append-only `action_executions` row (`started` → `completed` | `failed` | `interrupted`) with the provider calls it made (operation, HTTP status, duration; no cell contents) and whether a write was sent.
- **Executor (job `execute_action`), separate from the agent graphs.** (1) Take a lease on the action (`lease_expires_at`, 300 s); a concurrent worker that cannot take it does nothing. An unfinished `started` attempt from a crashed worker is closed as `interrupted`. (2) Re-check, in one transaction, before any external call and again before every write: plan `approved`, `approved_version` = action version = current version, the version's stored content hash = the action's = the applied approval's, the approver and the requester are still owners, the integration is still `verified` and still the action's spreadsheet, and the business matches the job. A failed check → `failed` with a code and no external call. (3) Insert the attempt row and **commit before the first external call**. (4) Read the tab metadata and the destination rows; create missing tabs and headers; a tab whose header differs is refused (`destination_layout_mismatch`). (5) Append only rows whose key is absent (`valueInputOption=RAW`, `insertDataOption=INSERT_ROWS`), at most one append per tab per round. (6) A timeout, connection reset or 5xx on a write is ambiguous: the next round re-reads before writing again, so a write is never repeated blindly. Reads and unambiguous failures (429, connect errors) retry with exponential backoff honouring `Retry-After` (capped at 60 s), bounded by `SHEETS_MAX_RETRIES` per call and three write rounds per job run; the job queue retries the run at most three times. (7) Read back every row of the action and compare cell by cell; record `external_ref` (spreadsheet ID, tabs, updated ranges) and `verification` (expected, found, missing, duplicated, mismatched keys).
- **Delivery guarantee.** Effectively-once per key within the destination as long as only ROOT writes the key column; this is not a universal exactly-once guarantee. The Sheets API has no idempotency token, so a person who deletes or edits rows between attempts, or a concurrent writer outside ROOT, can cause a re-append or a mismatch; mismatches go to `manual_review` and are never overwritten automatically.
- **Integration binding (ARCHITECTURE §11.2, SEC-17).** As designed, with two refinements: the global uniqueness of a spreadsheet is a unique index over `verified` bindings only (a pending, unproven binding cannot squat a spreadsheet), and a conflict returns 409 `integration_already_bound` without naming the other business; a business has at most one verified Sheets integration. Codes are stored as SHA-256, valid 30 minutes, single use.
- **Credentials.** `GOOGLE_SERVICE_ACCOUNT_JSON` (base64 of the service-account key file), backend only, held as a secret setting and never returned, logged or sent to the LLM or the browser. The token URI in the key file is ignored in favour of the constant Google endpoint. Tokens are cached in memory until shortly before expiry; a 401 refreshes once; a refused token exchange is `integration_credentials_invalid` (permanent). Scope `https://www.googleapis.com/auth/spreadsheets` on spreadsheets explicitly shared with the service account (least privilege available for writing).
- **Libraries.** `google-auth` signs the service-account JWT; HTTP goes through `httpx` (already a pinned dependency; moved to runtime) with explicit timeouts, instead of `google-api-python-client`, whose discovery client is synchronous and much larger. This amends ARCHITECTURE §11.1 and §13.1 and the "official client" wording of SEC-15.
- **Authorization.** New permissions `action:execute` and `action:retry` (owner only), `integration:read` (owner, analyst), `integration:manage` (owner). Retrying is allowed from `failed` and `manual_review` and always reconciles first. Tables `integrations`, `plan_actions` and `action_executions` carry `business_id`, RLS forced, and are in T-17/T-18; action payload, destination and binding columns are immutable (trigger), attempts are append-only except their own closing fields.
- **Persistence (migration `0007`).** `integrations`, `plan_actions` (replaces the baseline `recovery_actions` for this phase), `action_executions`; job type `execute_action`. Audit actions: `integration.created`, `integration.verified`, `integration.verification_failed`, `integration.revoked`, `action.requested`, `action.retry_requested`, `action.refused`, `action.succeeded`, `action.verified`, `action.failed`, `action.manual_review`.
- Reason: the user's Phase 8 brief (approved plans become verifiable external actions; Sheets primary; no customer messaging; explicit status set; duplicate prevention) and the R-13 requirement to document idempotency and reconciliation before implementation.
- Supersedes: ARCHITECTURE §10.4 per-action approval and §10.5 state names for this phase; §11.3 action types (replaced by `sheets.write_recovery_tasks`); DATA_CONTRACTS §6.4 `recovery_actions`/`approvals` and §7.8 routes for this phase.

### AMD-19 — 2026-10-17 — Phase 8: Apps Script bridge as a second way to reach Google Sheets

- Affects: AMD-18, ARCHITECTURE §11.1, SECURITY_MODEL SEC-15, DATA_CONTRACTS §7.9.
- **Why.** New Google Cloud projects enforce `iam.disableServiceAccountKeyCreation` ("secure by default"), so the AMD-18 service-account key cannot be downloaded without changing an organisation policy. The project owner found that path too heavy. A business can instead deploy a small Apps Script web app inside its own spreadsheet.
- **Bridge.** `services/api/integrations/apps_script/Code.gs` is container-bound: it only touches the spreadsheet it lives in (requests naming any other spreadsheet ID get 403). It requires a shared token of ≥ 32 characters, compared in constant time and sent in the JSON body, never in a URL. It offers exactly the five operations the executor uses (`titles`, `add_tabs`, `put_rows`, `read`, `append`), formats every cell it writes as plain text before writing (the equivalent of `RAW`), serialises requests with the document lock, caps rows (5,000) and cells (5,000 characters), and never deletes anything. It is deployed as a web app with *Execute as: Me*, *Who has access: Anyone*; the token is the access control.
- **Backend.** `SHEETS_BRIDGE_URL` (must match `^https://script\.google\.com/macros/s/[A-Za-z0-9_-]{20,200}/exec$`) and `SHEETS_BRIDGE_TOKEN` (secret), set together. When configured, the bridge is used instead of a service account; `GET …/integrations` reports `mode` (`apps_script` | `service_account`). `BridgeClient` shares the retrying transport and error classes of the REST client. Apps Script answers each POST with a redirect, and only one hop to `https://script.googleusercontent.com` is followed; any other `Location` is `integration_misconfigured`, as is a non-JSON answer (a sign-in page means the deployment is not public). Bridge statuses map like the API's: 401 → `integration_credentials_invalid`, 403 → `integration_permission_denied`, 404 → `destination_not_found`, other 4xx → `destination_invalid`, 5xx or busy → retryable and ambiguous for writes.
- **Unchanged.** Ownership proof (code in `ROOT_VERIFY!A1`, read through the bridge), the global one-business binding, payloads, keys, reconcile-before-write, read-back verification, statuses, retries and audit are exactly as in AMD-18. Outbound hosts (SEC-15) now also include `script.google.com` and, for that redirect only, `script.googleusercontent.com`.
- **Trade-offs.** Anyone with the URL and token can append to, and read, that one spreadsheet's tabs, so both are secrets kept only in backend configuration. Apps Script is slower (about 1–2 s per call) and subject to its daily quotas. The script runs as its deployer, so revoking that person's access, or deleting the deployment, stops execution (`integration_misconfigured`). The service-account mode remains for deployments that can use it.

### AMD-20 — 2026-10-17 — Phase 9: interface around the end-to-end workflow

- Affects: §8 baseline Phase 12 (the accessibility, end-to-end and performance part), DATA_CONTRACTS §7, ARCHITECTURE §13.1, TEST_STRATEGY (E2E).
- **Scope.** User Phase 9 delivers the interface of the end-to-end workflow. It has three main screens, Business health, the Investigation workspace and the Decision center, plus Investigations, Data, Integrations and Evidence views. It adds Playwright end-to-end, accessibility (axe, WCAG 2.2 AA tags), mobile, keyboard, failure-mode and performance checks. Backend hardening items of baseline Phase 12 (load tests, deletion and retention, account lifecycle) are not part of it.
- **API additions (read-only, `investigation:read`, tenant-scoped, keyset pagination as in §7.1):**
  - `GET /businesses/{bid}/investigations` gains `cursor` and returns `next_cursor`, ordered by id descending. The id is UUIDv7, so this is creation order; it replaces `created_at, id`, which gives the same order.
  - New `GET /businesses/{bid}/recovery-plans?limit&cursor`: every plan of the business, newest first, with `execution_status` (the latest `plan_actions` status or null). This is the Decision center index.
- **Interface rules.**
  - Every screen is a React Server Component fed only by these APIs. Client components are limited to inputs, charts and controls that need state.
  - Facts, hypotheses, estimates, people's input and actions are labelled with a text badge, never by colour alone.
  - Each chart is paired with a table of the same numbers.
  - Lists are paginated on the server.
  - Nothing is shown as done until the API confirms it.
  - Untrusted text is rendered only as React text.
- **Dependencies.** `recharts` 3.10.1 (listed in §13.1, first used now) with its peer `react-is` 19.3.1, and `@axe-core/playwright` 4.13.0 (dev, listed in §13.1). 4.14.0 was skipped because it was published two days earlier.
- Reason: the user's Phase 9 brief.
- **Bridge behaviour observed in the real end-to-end run (amends AMD-19).**
  - Google sometimes redirects the result URL of an Apps Script call more than once, and in about 1 of 16 probed calls the chain ended on Google's HTML "Error" page after ~19 s. Healthy calls took 2–19 s.
  - **Redirects:** `BridgeClient` now follows at most three hops between `script.google.com` and `script.googleusercontent.com`, never to another host.
  - **Error page:** a non-JSON answer reached through Google's own redirect chain is `provider_unavailable`. It is retryable, and ambiguous for writes, so it is reconciled by reading before any rewrite. It is no longer `integration_misconfigured`. A non-JSON answer straight from the deployment URL (sign-in page, removed deployment) is still `integration_misconfigured`.
  - **Timeout:** the default `SHEETS_TIMEOUT_SECONDS` rises from 20 to 45.

### AMD-21 — 2026-10-17 — Phase 10 technical hardening audit

- Affects: phase numbering after AMD-17/20; ARCHITECTURE §4, §10–§11; SECURITY_MODEL SEC-04, SEC-13, SEC-20, SEC-24; TEST_STRATEGY regression and performance evidence.
- **Scope.** The user's Phase 10 instruction is a security, correctness, reliability and performance audit of the existing application, replacing the earlier mapping of Phase 10 to deployment. Deployment, account-lifecycle features, deletion/retention features, and all Phase 11 work are not started. Audit all current source code and existing tests, reproduce material issues, remediate them and add regressions. No extra infrastructure or dependencies without measured justification.
- **Existing contracts completed.** Enforce the already-specified per-authenticated-session 300/minute global limit, keeping unauthenticated login protections separate. Serialize concurrent account/IP login-throttle decisions. Recheck approval and integration authorization before each externally mutating call, including implicit tab/header creation. Reconcile interrupted writes conservatively rather than treating unknown outcomes as safe failures. Fence job completion, heartbeat and failure updates to the lease attempt that acquired the job.
- **Evidence and gates.** Run all existing automated suites, migrations, negative authorization and fault-injection tests, frontend/browser checks, dependency/secret scans and bounded local concurrency/load/performance measurements. Report exact counts and measured timings, distinguish fakes from real integrations, and do not claim unexecuted live-provider or production checks. Completion requires no known critical project issue, no unresolved project-introduced high-severity vulnerability, no failing critical tests and a working full workflow. Lower-severity exceptions and unsupported deferred features are explicit findings.
- Reason: the user's Phase 10 hardening brief and concrete audit findings; speed is addressed through parallel review/testing, not by lowering gates.
- Supersedes: the previous Phase 10 deployment assignment only. Existing financial formulas, approval requirements, test/coverage thresholds and the prohibition on later-phase work stand.

### AMD-22 — 2026-10-17 — Phase 11 production deployment on Alternate Futures

- Affects: §8 baseline Phase 13 (deployment), ARCHITECTURE §14–§15 (ADR-010), SECURITY_MODEL §2, §7, §11, TEST_STRATEGY §3, §5 (T-53).
- **Scope.** User Phase 11 delivers production deployment on **Alternate Futures** (`Alternate Clouds` via the `@alternatefutures/acc` CLI and containerized OCI services), replacing the baseline Vercel + Railway topology while preserving all application, database, security and workflow contracts.
- **Containerized service topology on Alternate Futures.**
  - **Frontend (`apps/web/Dockerfile`, port `3000`):** Next.js 16 standalone production server built in a multi-stage Node 24 Alpine image and executed as non-root user `rootapp` (`UID 10001`, `GID 10001`), publicly exposed at `https://<slug>-app.alternatefutures.ai`.
  - **Backend (`services/api/Dockerfile`, port `8000`):** FastAPI + embedded PostgreSQL job worker + LangGraph runtime built in a multi-stage Python 3.13 slim image from locked `uv.lock` dependencies and executed as non-root user `rootapp` (`UID 10001`, `GID 10001`), exposed at `https://<slug>-api.alternatefutures.ai`. Runs as a single Uvicorn worker process (`--workers 1 --proxy-headers --forwarded-allow-ips=*`) so the in-process `SEC-20` session token bucket (`300 req/min`) and embedded background lease worker remain authoritative without multi-process split-brain.
- **Database & LangGraph persistence.** Connects to external managed PostgreSQL over TLS (`sslmode=require`) with strict role separation (`AMD-3`, `SEC-06`): schema migrations (`alembic upgrade head`) execute using the table-owner role via `DATABASE_URL_MIGRATIONS`, whereas the application and worker run solely via `DATABASE_URL` as the least-privilege `root_app` role (`NOSUPERUSER`, `NOBYPASSRLS`, RLS enabled and verified by `/readyz`). Durable LangGraph state persists in the PostgreSQL `langgraph` schema (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`) across container restarts.
- **Runtime same-origin API proxy (`ADR-010`).** Next.js build-time `rewrites()` bake `BACKEND_URL` during image build; for portable container images, `apps/web/src/proxy.ts` rewrites `/api/v1/:path*` at request time using the runtime `BACKEND_URL` environment variable (in addition to `next.config.ts` fallback rewrites) while still generating per-request CSP nonces on page routes. Browser traffic stays same-origin on the public frontend HTTPS origin (`https://<slug>-app.alternatefutures.ai`), keeping `__Host-root_session` and `__Host-root_csrf` cookies first-party (`Secure; HttpOnly; SameSite=Lax; Path=/`), while backend `FRONTEND_ORIGIN` enforces strict CORS and CSRF `Origin` validation (`SEC-03`, `SEC-25`).
- **Rollback & operational recovery contract.**
  - **Immutable image tags:** Alternate Clouds caches and refuses in-place re-deploys on mutable `:latest` or `:main` tags. Every deployment builds and pushes an explicit versioned OCI tag (e.g. `:v1.0.0`, `:sha-<commit>`), and application rollback redeploys the previous known-good tag via `acc services deploy <id> --image <repo>:<previous-tag>`.
  - **Database migrations:** Rolling back an application container image does *not* automatically reverse Alembic schema migrations. All schema changes follow **expand → migrate → contract** (`R-14`) so the previous container image remains compatible with the latest schema; emergency schema recovery restores the pre-deploy database snapshot.
  - **In-flight investigations & external actions:** Interrupted `run_investigation` and `run_recovery_plan` jobs recover after lease expiry (`lease_expires_at`) and resume from PostgreSQL LangGraph checkpoints under per-thread advisory locks (`AMD-13`, `AMD-17`). Interrupted `execute_action` Google Sheets jobs mark unfinished attempts `interrupted` and reconcile via read-back before writing (`AMD-18`, `AMD-21`), escalating ambiguous outcomes to `manual_review` rather than duplicating external writes.
- Reason: Fulfill the user's Phase 11 requirement to package, deploy and verify the complete ROOT 2.0 stack on Alternate Futures container infrastructure with full security, persistence and rollback guarantees.
- Supersedes: Baseline Vercel + Railway platform references in ARCHITECTURE §14 and SECURITY_MODEL §2, §7, §11.

### AMD-23 — 2026-10-17 — Redesigned sign-in page (branch `ui-redesign`)

- Affects: ARCHITECTURE §13.1 (frontend dependencies).
- **Scope.** UI only. `/login` gets a dark, animated layout: orbiting product icons and a ripple on large screens, and the form alone on small ones. The login request, the error mapping and the redirect behaviour are unchanged.
- **Form.** Inline checks (required, email shape, password length within the server policy of 12–128 characters) run before any request. The form keeps `onSubmit` with `preventDefault` and declares `method="post"`, so credentials never go into a URL. The submit button stays disabled until hydration and while a request is in flight. There is a show/hide toggle for the password.
- **Accessibility.** Animations stop under `prefers-reduced-motion`, and the decorative panel is `aria-hidden`.
- **Dependencies.** `motion` 14.0.0 (animation) and `lucide-react` 1.50.0 (icons, bundled, with no remote images). Both are exact pins of releases at least two weeks old. They are used only by `src/components/ui/modern-animated-sign-in.tsx`.
- Reason: the user's sign-in redesign brief.

### AMD-24 — 2026-10-17 — Phase 12 read-only PostgreSQL business source
- **Scope.** A second way to supply the four canonical files: a read-only connection to the business's own PostgreSQL. Extraction writes the selected tables as canonical CSV and hands them to the existing `inspect_file` → `create_dataset` path, so validation, mapping, analytics, detection, investigation and execution are shared; there is no second analytics path.
- **Data.** New tenant table `source_connections` (RLS as AMD-3, no password column). `datasets` gains `source_type` (`upload` | `postgres`, default `upload`) and nullable `source_connection_id`. Migration 0008, reversible.
- **Security.** The password is never stored, logged or returned; each sync asks for it. The host is resolved once and every resolved address must be public (loopback, private, link-local, multicast, reserved and 169.254.169.254 are refused) unless listed in `SOURCE_DB_ALLOWED_HOSTS`; the connection pins the resolved IP (`hostaddr`) to prevent DNS rebinding. TLS `verify-full` by default (`SOURCE_DB_SSLMODE=require` permitted for providers without a public CA chain; `disable` only in the test environment). `default_transaction_read_only`, `BEGIN READ ONLY`, 10 s connect and 20 s statement timeouts, per-kind row cap = the CSV `MAX_ROWS`. Schema/table names must match `^[a-z_][a-z0-9_]{0,62}$`, exist in `information_schema`, and are quoted with `sql.Identifier`; only canonical columns are selected. Driver errors map to fixed messages. Create and sync are audited; sync shares the upload rate limit.
- **Synchronization.** Manual "Sync now", full refresh only: no trustworthy change-tracking contract exists yet, so incremental sync is **not** claimed. An unchanged source yields the same checksum and is reported as `unchanged`; a changed source creates a new immutable dataset version, so investigations stay tied to their snapshot. No scheduled sync.
- **Not delivered.** XLSX import, other database engines, incremental sync.
- Reason: the user's Phase 12 brief (dual-source onboarding).
