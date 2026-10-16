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
