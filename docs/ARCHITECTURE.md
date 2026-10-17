# ROOT 2.0 — Architecture

> Baseline created in Phase 1. Requirement IDs (FR, NFR, SEC) refer to [ROOT_MASTER_PLAN.md](ROOT_MASTER_PLAN.md) §6 and [SECURITY_MODEL.md](SECURITY_MODEL.md) §3.
> Data shapes are defined in [DATA_CONTRACTS.md](DATA_CONTRACTS.md). Where this document and DATA_CONTRACTS disagree, DATA_CONTRACTS wins for shapes and formulas, and this document wins for behaviour.

## 1. Architectural principles

1. **Facts come from code. Interpretation comes from the LLM.** Every number shown to a user is computed by deterministic code from stored data (ADR-004).
2. **Nothing is overwritten.** Datasets, analysis revisions, evidence and events are append-only. Human corrections layer on top (ADR-007).
3. **The agent proposes and the human disposes.** The LLM has no path to a side effect. Execution is a separate, authorised, idempotent subsystem (ADR-009).
4. **One of everything.** One backend service, one frontend app, one database (ADR-001, ADR-002).
5. **Fail recoverably.** Every long-running step is a job with a lease or a checkpointed graph node. Every external call is reconcilable or marked `outcome_unknown`.

## 2. System context

```
               ┌──────────────────────── Browser (Owner / Analyst / Viewer) ───────────────────────┐
               │                       Next.js UI (React, shadcn/ui, Recharts)                     │
               └───────────────────────────────┬───────────────────────────────────────────────────┘
                                               │ HTTPS, same-origin /api/* (Vercel rewrite)
┌──────────────────────── Vercel ──────────────▼──────────────┐
│ Next.js app: pages, server components, rewrite /api/* ───────┼──────┐
└──────────────────────────────────────────────────────────────┘      │ HTTPS
┌──────────────────────── Railway ─────────────────────────────────────▼──────────────────────┐
│ FastAPI service (single deployable)                                                          │
│  ├─ HTTP API (/api/v1)          ├─ Job worker (asyncio task in app lifespan)                 │
│  ├─ Auth, RBAC, tenant context  ├─ LangGraph investigation runtime                           │
│  ├─ Ingestion & validation      ├─ Action executor                                           │
│  ├─ Analytics (SQL + pandas)    └─ LLM gateway ──────────────────────────► LLM provider API  │
│  └─ Integrations adapters ───────────────────────────────────► Google Sheets API, Slack hook │
│                         │                                                                    │
│                         ▼                                                                    │
│ Managed PostgreSQL: domain tables, RLS, jobs, events, audit, LangGraph checkpoints           │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

Trust boundaries are TB-1 to TB-6 (SECURITY_MODEL §2).

## 3. Components and module layout

```
backend/app/
  main.py                 app factory, lifespan (starts worker), middleware
  core/                   settings, db (engine, session, tenant context), logging, errors, security headers
  auth/                   users, sessions, CSRF, throttling
  tenancy/                businesses, memberships, permissions (require_permission)
  audit/                  audit writer
  ingestion/              upload handling, CSV parsing, validation rules, normalization
  analytics/              metrics.py, segmentation.py, detection.py, queries/ (parameterised SQL), hashing.py
  investigation/
    graph.py              LangGraph definition
    state.py              InvestigationState
    nodes/                one module per node (ARCHITECTURE §12.3)
    hypotheses/catalog.py hypothesis templates and verification criteria
    evidence/tools.py     evidence tool registry
    verification.py       deterministic status and confidence
    events.py             event writer + projection reducer (replay)
  hitl/                   clarifications, human inputs, overlays, findings review
  recovery/               strategies.py, estimator.py, actions.py, approvals.py
  integrations/           sheets.py, slack.py, crypto.py
  jobs/                   queue.py (enqueue, lease, heartbeat), worker.py, handlers registry
  llm/                    gateway.py, contracts.py, validation.py, prompts/ (versioned .md)
  api/v1/                 routers (thin: validation → service → response)
backend/migrations/       Alembic
backend/scripts/          generate_dataset.py (dev/test only, never imported by app/)
backend/tests/            unit/, integration/, security/, perf/
frontend/
  app/                    App Router routes
  components/             ui/ (shadcn), domain components
  lib/                    api client (generated types), polling hook, formatters
  tests/                  vitest
  e2e/                    playwright
```

Layering rule: `api → services (domain modules) → repositories/queries → db`. Domain modules never import `api`. `investigation/nodes` call domain services. They never issue raw SQL directly.

## 4. Runtime architecture

### 4.1 Request flow

1. The browser calls `https://<frontend>/api/v1/...`. Vercel rewrites it to the backend. Cookies stay first-party (ADR-010).
2. Middleware order: request ID → security headers → body-size limit → session load → CSRF check (unsafe methods) → route.
3. Route dependencies: `current_user` → `tenant_context(business_id)` (membership check, then `SET LOCAL app.current_business_id`) → `require_permission(...)`.
4. The handler validates input (Pydantic strict), calls a service, and commits a transaction that writes domain rows, audit rows and investigation events **atomically**.
5. Errors are mapped to RFC 9457 problem details (DATA_CONTRACTS §7.1).

### 4.2 Tenant context

Every tenant-scoped table has `business_id NOT NULL` and an RLS policy `business_id = current_setting('app.current_business_id')::uuid` (SECURITY_MODEL §6). Repositories also filter by `business_id` explicitly, giving two independent controls. Jobs carry `business_id`, and the worker sets the same session variable before running a handler.

### 4.3 Job queue and worker (ADR-003)

- Table `jobs` (DATA_CONTRACTS §6). Job types: `ingest_dataset`, `run_investigation`, `resume_investigation`, `execute_action`, `retention_sweep`.
- Enqueue happens in the same transaction as the triggering domain change.
- The worker runs as an asyncio task started in FastAPI's lifespan, with concurrency `WORKER_CONCURRENCY` (default 2). It polls every 1 s with:
  `SELECT … FROM jobs WHERE status='queued' AND run_after<=now() ORDER BY priority, id FOR UPDATE SKIP LOCKED LIMIT 1`. It then sets `status='running'`, `lease_expires_at=now()+interval '120 s'` and `attempts=attempts+1`.
- Heartbeat: the lease is extended every 30 s while running.
- Lease recovery: jobs with `status='running'` and an expired lease return to `queued`. Handlers must be idempotent (§12.5, §10.5).
- Retry policy is per type: `max_attempts` (ingest 3, investigation 3, execute_action 5 for transient errors only), with exponential backoff `2^attempt × 5 s` and ±20% jitter. Non-retryable errors set `failed` immediately.
- Unique `dedupe_key` (for example `execute_action:<action_id>:<payload_hash>`) prevents duplicate enqueueing.
- Graceful shutdown: stop leasing, wait ≤ 25 s for running handlers, then leave the remaining leases to expire.

### 4.4 Investigation lifecycle

> **Amended by AMD-15 (Phase 6):** implemented statuses are `queued`, `running`, `awaiting_human` (clarification only), `completed`, `completed_no_incidents`, `insufficient_data` and `failed`; `cancelled` and the other interrupt kinds are not yet implemented.

```
created ─► queued ─► running ─┬─► awaiting_human ─(answer)─► queued ─► running ─┐
                              │      (clarification | findings_review |        │
                              │       plan_constraints | action_decisions)     │
                              ├─► completed                                     │
                              ├─► completed_no_incidents                        │
                              ├─► insufficient_data                             │
                              ├─► failed (recoverable: owner may retry → queued)│
                              └─► cancelled (owner/analyst, any non-terminal) ◄─┘
```

`investigations.phase` holds the fine-grained graph position (`computing`, `detecting`, `hypothesizing`, `gathering_evidence`, `verifying`, `synthesizing`, `planning`). One investigation is bound to one dataset version and may span several analysis revisions (one per overlay version).

### 4.5 End-to-end execution flow

```
POST /datasets ─► job ingest_dataset ─► dataset.ready
POST /investigations ─► job run_investigation ─► LangGraph:
  load_context → compute_metrics → detect_incidents → select_incident → generate_hypotheses
  → gather_evidence → verify_hypotheses → assess_human_need ─┬─► await_clarification (interrupt)
                                                             └─► next incident | synthesize_findings
  → await_findings_review (interrupt) → await_plan_constraints (interrupt) → plan_recovery
  → await_action_decisions (interrupt) → finalize
POST /clarifications/{id}/answer ─► tx(answer + event + job resume_investigation) ─► graph resumes
POST /actions/{id}/approve ─► tx(approval + event + job execute_action) ─► executor ─► Sheets/Slack
```

## 5. Incident detection (FR-07)

All constants live in `analysis_config` (DATA_CONTRACTS §3.5) and are versioned. The algorithm is deterministic and never refers to specific entities.

### 5.1 Time frame (ADR-017, ADR-019)

- `T` = as-of date = the maximum `order_date` in the revision's effective data.
- Weeks are ISO weeks (Monday–Sunday). The week containing `T` is excluded unless `T` is a Sunday (complete weeks only).
- **Current window W** = the last `current_weeks` (4) complete weeks.
- **Baseline B** = up to `baseline_weeks` (12) complete weeks immediately before W.
- Detection requires ≥ `min_baseline_weeks` (8) baseline weeks with ≥ 1 order at the overall level. Otherwise the run ends with `insufficient_data` and no incidents (a dataset needs at least 12 complete weeks).

### 5.2 Monitored metrics and adverse direction

| Metric (DATA_CONTRACTS §3.3) | Adverse direction | Kind |
|---|---|---|
| `net_revenue` | down | amount |
| `gross_margin_pct` | down | rate |
| `refund_rate` | up | rate |
| `discount_rate` | up | rate |
| `active_customers` | down | count |
| `repeat_customer_rate` | down | rate |
| `aov` | down | amount |

### 5.3 Slices

- `overall`
- each value of `category`, `region`, `channel`
- `product`, limited to the products that together make up the top 80% of baseline net sales, at most 50
- `customer_segment`, using segments computed as of the last day of B so the slice does not depend on the current window

Minimum support: the slice has ≥ `min_slice_orders_baseline` (30) orders in B and ≥ `min_slice_orders_current` (10) in W. For `refund_rate`, it also needs ≥ 30 units sold in W.

### 5.4 Test per (metric, slice)

1. Compute weekly values `x_b` for B and `x_w` for W.
2. `m = median(x_b)`, `MAD = median(|x_b − m|)`, `σ̂ = 1.4826 × MAD`.
3. `c = mean(x_w)` (for rates, the window aggregate ratio Σnum/Σden, not the mean of weekly ratios).
4. Robust z: `z = (c − m)/σ̂` if `σ̂ > 0`, else `z = undefined` (the z condition is then skipped and the change thresholds decide alone).
5. Adverse-signed change: `Δabs = c − m`, `Δrel = (c − m)/|m|` (undefined if m = 0).
6. **Flag** when all of the following hold:
   - The direction is adverse.
   - `|z| ≥ z_threshold` (3.0) or z is undefined.
   - For amounts and counts: `|Δrel| ≥ rel_change_threshold` (0.15). For rates: `|Δabs| ≥ rate_abs_threshold_pp` (0.02) **and** `|Δrel| ≥ rate_rel_threshold` (0.20).
   - `impact ≥ materiality` (§5.5).
7. **Onset week:** the first week in W whose value is adverse beyond `m ± 3σ̂`, or beyond the change thresholds when σ̂ = 0.

### 5.5 Financial impact (window W, in dataset currency, computed in NUMERIC)

Rates use **baseline aggregate** ratios (Σnum/Σden over B) for impact. Medians are used only for the statistical test.

| Metric | Impact formula |
|---|---|
| `net_revenue` | `n_W × median_weekly(B) − Σ_W net_revenue` |
| `gross_margin_pct` | `(margin_B − margin_W) × net_revenue_W` |
| `refund_rate` | `(refund_rate_W − refund_rate_B) × net_sales_W` |
| `discount_rate` | `(discount_rate_W − discount_rate_B) × gross_sales_W` |
| `active_customers` | `(median_weekly_active(B) − mean_weekly_active(W)) × n_W × net_revenue_per_active_customer_week_B` |
| `repeat_customer_rate` | `(rate_B − rate_W) × Σ_W weekly_active_customers × net_revenue_per_active_customer_week_B` |
| `aov` | `(aov_B − aov_W) × orders_W` |

Here `n_W` is the number of weeks in W. `materiality = max(materiality_pct (0.01) × overall net_revenue_W, min_impact_amount (0.00))`. Impacts are per incident and are **not additive** across incidents. The UI states this.

### 5.6 Consolidation and ranking

1. **Parent suppression.** For the same metric, if a child slice's impact ≥ `child_share_threshold` (0.60) × its parent's impact (overall is the parent of every slice), the parent is not emitted separately. It is attached as `context.parent` of the child.
2. **Overlap merge.** For the same metric, flagged slices from different dimensions whose W order sets have Jaccard ≥ 0.60 are merged. The highest-impact slice is primary and the others are `related_slices`.
3. **Rank** by impact descending, then `|z|` descending (undefined counts as 0), then incident key `metric|dimension|value` ascending.
4. Keep the top `max_incidents` (5). Investigate the top `max_investigated_incidents` (3). The others are listed as `not_investigated`.

### 5.7 Outputs

Each incident stores: key, metric, dimension and value, W and B date ranges, `m`, `σ̂`, `c`, z, Δabs, Δrel, onset week, impact, weekly series, related slices, and the evidence ID of its detection record (an `incident_detection` evidence item, so the detection itself is traceable).

## 6. Hypotheses and evidence (FR-08, FR-09, FR-10)

### 6.1 Approach (ADR-014)

Hypotheses come from a versioned **catalog** of templates. Applicability rules select candidates deterministically. The LLM may prioritise candidates, propose catalog templates with parameters (validated), and request extra evidence-tool calls from the allowlist. It cannot invent a hypothesis type without verification criteria.

### 6.2 Hypothesis catalog v1

> **Amended by AMD-12 (Phase 5):** `H_CUSTOMER_CHURN` is renamed `H_SEGMENT_SHIFT`; `H_MIX_SHIFT` is deferred. The catalog as built is in DATA_CONTRACTS §12.3.

| Template | Applicable metrics | Claim | Required evidence tools |
|---|---|---|---|
| `H_RETURNS_QUALITY` | refund_rate, gross_margin_pct, net_revenue | A product or category quality problem drives returns | `reason_mix_shift`, `contribution_by_dimension(product)`, `cohort_return_rate` |
| `H_FULFILLMENT` | refund_rate, active_customers, repeat_customer_rate | Delivery problems (late delivery, wrong item) in a region or channel | `reason_mix_shift`, `contribution_by_dimension(region)` |
| `H_PRICE_DISCOUNT` | discount_rate, gross_margin_pct, aov, net_revenue | Discounting increased or price realisation fell | `discount_shift`, `price_realization` |
| `H_MIX_SHIFT` | gross_margin_pct, aov, net_revenue | Sales mix moved toward lower-value or lower-margin products | `mix_rate_decomposition` |
| `H_CUSTOMER_CHURN` | net_revenue, active_customers, repeat_customer_rate | Loss of previously active, valuable customers | `segment_retention`, `contribution_by_dimension(customer_segment)` |
| `H_CONCENTRATION` | net_revenue, aov | A few customers explain most of the change | `top_customer_contribution` |
| `H_CHANNEL` | net_revenue, active_customers | A specific channel underperforms | `contribution_by_dimension(channel)` |
| `H_DATA_QUALITY` | all | The change is an artefact (missing days, duplicates, test orders) | `data_completeness`, `duplicate_pattern`, `top_customer_contribution` |
| `H_EXTERNAL_CONTEXT` | all | A cause the data cannot show (stockout, supplier change, promotion, seasonality event) | none (human context only) |

Applicability: every template whose metric list contains the incident's metric is a candidate. `H_DATA_QUALITY` and `H_EXTERNAL_CONTEXT` are always candidates. At least two competing candidates are always investigated.

### 6.3 Evidence tools (registry, ADR-004)

> **Amended by AMD-12 (Phase 5):** the tool list as built is `metric_breakdown`, `period_comparison`, `return_investigation`, `segment_analysis`, `customer_exposure`, `evidence_lookup` and `data_quality_check` (DATA_CONTRACTS §12.2).

Each tool has a Pydantic parameter model, a parameterised SQL query (dimensions resolved through an allowlist and never interpolated from input), a deterministic Python post-processor, and an output model. The tools:

| Tool | Parameters | Output (all numbers computed) |
|---|---|---|
| `contribution_by_dimension` | metric, dimension, incident scope | per value: baseline, current, delta, share of total delta |
| `reason_mix_shift` | scope | per reason_code: share of refunds B vs W, pp change, contribution to excess refunds |
| `cohort_return_rate` | scope, maturity_days (= `return_maturity_days`) | per order-week cohort: matured refund rate |
| `discount_shift` | scope | discount rate B vs W per dimension value. Share of lines discounted |
| `price_realization` | scope | (net_sales / (qty × list_price)) B vs W |
| `mix_rate_decomposition` | scope, dimension (product or category) | volume, mix and rate effects that sum exactly to the total change |
| `segment_retention` | scope | per segment: active customers B vs W, net revenue delta |
| `top_customer_contribution` | scope, k ≤ 20 | top-k customers' share of the delta (pseudonymous IDs only) |
| `data_completeness` | scope | days with zero orders, row-count anomalies per week |
| `duplicate_pattern` | scope | count and value of near-duplicate lines (same customer, product, qty, price, date) |
| `incident_detection` | incident | the §5.7 statistics (recorded at detection) |

Evidence item record (DATA_CONTRACTS §6): `tool`, `params` (canonical JSON), `revision_id`, `query_version`, `result` (JSON), `result_hash = sha256(canonical_json(result))`, `source` (`data` or `human_context`), `created_by` (`system`, `llm_request` or `user`). Evidence rows are never updated.

Limits: ≤ 6 LLM-requested tool calls per hypothesis and ≤ 40 evidence items per incident.

### 6.4 Verification and confidence (deterministic)

> **Amended by AMD-15 (Phase 6):** human answers are evaluated as specified here, with human-only templates following the answer, data refutation always winning, contradictions recorded as `conflicts`, and an `intended` interpretation that keeps a supported mechanism out of the root-cause candidate.

> **Amended by AMD-12 (Phase 5):** a data criterion without evidence is *unknown*, and a hypothesis with an unknown data criterion is `inconclusive` rather than refuted by low confidence.

Each template defines criteria `C_i` with weight `w_i`, a polarity (`support` or `refute`) and an evaluation function over evidence (and over human context for criteria marked `source=human`). Example for `H_RETURNS_QUALITY`:

| Criterion | Polarity | Weight | Rule |
|---|---|---|---|
| C1 | support | 0.4 | `damaged` + `defective` share of refunds rises by ≥ 10 pp from B to W |
| C2 | support | 0.3 | One product or category explains ≥ 50% of the excess refunds |
| C3 | support | 0.3 | Matured cohort refund rate for onset-week cohorts exceeds B by ≥ 2 pp |
| C4 (human) | support | 0.3 | Human confirms a product, supplier or batch change in the period |
| R1 | refute | — | `damaged` + `defective` share falls or rises by < 2 pp **and** C2 is false |

The full criterion tables for every template are implemented in `hypotheses/catalog.py` (Phase 6). They are reviewed against this structure, and any table change is a catalog version bump recorded in the log.

Scoring:

- `data_confidence = Σ w_i (satisfied support criteria, source=data) / Σ w_i (all support criteria, source=data)`.
- `human_confidence` = the same over human criteria (null if none were answered).
- `confidence = min(1, data_confidence + 0.5 × human_weight_satisfied)`, shown together with the split.

Status:

- `refuted` if any refute criterion holds.
- else `needs_human_context` if the template is human-only (`H_EXTERNAL_CONTEXT`) or a human criterion exists, is unanswered, and `data_confidence ∈ [0.3, 0.7)`.
- else `supported` if `confidence ≥ 0.7`.
- else `refuted` if `confidence < 0.3` and all required tools ran.
- else `inconclusive`.
- `rejected_by_human` is a separate terminal flag that overrides the status for ranking (§7.5).

Root-cause candidate = the highest-confidence `supported` hypothesis not rejected by a human, with ties broken by catalog order.

## 7. Human-in-the-loop (FR-11 to FR-15)

### 7.1 Interrupt types

> **Amended by AMD-15 (Phase 6):** the `clarification` interrupt is implemented (DATA_CONTRACTS §13): one question at a time, answer options carry catalog effects (confirm, deny, intended, unsure), free text is stored but never parsed, and trigger **CT-5 (intent)** is added to §7.2. `findings_review`, `plan_constraints` and `action_decisions` remain later work.

| Interrupt | Created by | Who may answer | Answer effect |
|---|---|---|---|
| `clarification` | `assess_human_need` | owner, analyst | context evidence, correction, hypothesis rejection, or skip |
| `findings_review` | `synthesize_findings` | owner, analyst | accept → planning. Challenge → re-investigate |
| `plan_constraints` | after findings accepted | owner, analyst | constraints + assumption values → plan generation |
| `action_decisions` | `plan_recovery` | owner (approve), owner or analyst (edit, reject) | the graph finalizes when no action remains `proposed` |

### 7.2 Clarification triggers (deterministic)

- **CT-1 Ambiguity:** the top two non-refuted hypotheses' confidences differ by < 0.15.
- **CT-2 Required context:** the leading hypothesis is `needs_human_context`.
- **CT-3 Data suspicion:** `H_DATA_QUALITY` has ≥ 1 support criterion satisfied (for example one customer > 30% of the slice delta, or near-duplicates).
- **CT-4 Assumption gap:** a candidate requires a human-only criterion (C4-type) to separate it from a rival.

Caps: 3 per incident and 6 per investigation. When a cap is hit, the graph proceeds with current statuses and records `clarification.cap_reached`.

Question **type and options are set by rule** (catalog `question_spec` per criterion). The LLM only phrases the text and explanation within the placeholder contract. Answer types: `single_choice`, `multi_choice`, `boolean`, `free_text` (≤ 1000 chars, stored as untrusted), `date_range`, `record_selection` (for corrections).

### 7.3 Context answers (FR-12)

The answer is stored in `human_inputs` (kind `context`) and becomes an evidence item with `source=human_context`, attributed to the user, linked to the clarification, and with a structured value (option ID or date range). Criteria with `source=human` evaluate against it. Free text is shown but never parsed into numbers or used as an instruction.

### 7.4 Corrections (FR-13, ADR-007)

Correction kinds (`data_overlays.rules`, versioned per investigation):

| Kind | Effect on the effective dataset |
|---|---|
| `exclude_records` | Exclude listed `order_line_id`s or all lines of listed `customer_id`s, with a reason |
| `reclassify_return_reason` | Override `reason_code` for listed `return_id`s |
| `exclude_baseline_period` | Drop a date range from baseline B (for example a one-off promotion) |

Flow: the correction is validated (IDs exist in the dataset version and the business matches) → a new `data_overlays` version (N+1) = previous rules + new rule → a new `analysis_revisions` row `(dataset_version, overlay_version N+1, config_version, code_version)` → the graph routes back to `compute_metrics` on the new revision. Human inputs already given (context answers, hypothesis rejections, answered clarifications) are keyed by **incident key + template / criterion key**, not by per-revision row IDs, so they carry over to matching incidents in the new revision and are not asked again. **Original dataset rows and earlier revisions' metrics and evidence are never modified.** Effective data = base tables filtered and overridden by the overlay through SQL views parameterised by `overlay_version`. Revision compare (§9.4) shows the diff.

### 7.5 Hypothesis rejection (FR-14)

Rejection requires a reason (≤ 1000 chars). It sets `rejected_by_human` (stored as a human input and an event; evidence is untouched). The graph re-ranks. If no supported hypothesis remains, CT-1/CT-2 may fire, or the findings report "no supported root cause" honestly.

### 7.6 Findings review (FR-15)

Accept → planning. Challenge needs a reason and either a selected alternative hypothesis or new context. The graph returns to `generate_hypotheses` for the incident with the challenge as human context. At most 2 challenges per investigation. After that, findings are finalised as "disputed", with both views recorded.

### 7.7 Persistence and resume strategy (every checkpoint)

> **Amended by AMD-15 (Phase 6):** for `clarification` the stored row moves `open → awaiting → answered → applied`; the answer must quote the paused `checkpoint_id`; the resume job is `run_investigation` with dedupe key `run_investigation:<id>:resume:<clarification_id>`, and the worker resumes only while the latest checkpoint equals the stored one.

| Checkpoint | Persisted before pausing | Resume trigger | Exactly-once guard | Crash between answer and resume |
|---|---|---|---|---|
| `clarification` | `clarifications` row (status `open`), event `clarification.requested`, LangGraph checkpoint at `interrupt()` | `POST …/answer`: tx writes the answer (`answered`), `human_inputs`, the event, and job `resume_investigation(dedupe_key=clarification_id)` | Unique dedupe key. The node checks `status='answered'` and sets `applied` in the same tx as its effects | The job stays queued or leased and is re-run by the worker. The graph resumes from the checkpoint |
| `findings_review` | `findings` row (status `pending_review`), checkpoint | `POST …/findings/{id}/review` | dedupe_key = findings_id + review_seq | same |
| `plan_constraints` | `recovery_plans` row (status `awaiting_constraints`), checkpoint | `POST …/recovery-plan/constraints` | dedupe_key = plan_id | same |
| `action_decisions` | `recovery_actions` rows (`proposed`), checkpoint | Each decision endpoint. The finalizer job is enqueued when no action remains `proposed` | dedupe_key = plan_id + `finalize` | same. Approvals also enqueue execution independently (§10.5) |

LangGraph re-executes an interrupted node from its start on resume. Therefore code before `interrupt()` in a node is limited to idempotent reads and upserts keyed by deterministic IDs (§12.5).

## 8. LLM integration (ADR-004, ADR-016)

### 8.1 Gateway

`llm/gateway.py` exposes `generate(task, input_model, output_model, tools?) -> output_model`. Responsibilities: provider call through the official SDK with JSON-schema structured output, timeout (60 s), transient-error retry (2), budget check (NFR-10), recording to `llm_calls`, and running the validation pipeline. Temperature is the provider's lowest deterministic setting. Prompts are versioned files `llm/prompts/<task>.v<N>.md`.

### 8.2 LLM tasks

> **Amended by AMD-16:** the gateway's provider is Anthropic or Groq (`LLM_PROVIDER`). Both adapters implement the same protocol; validation, budgets, repair and fallback are provider-independent.

> **Amended by AMD-12 (Phase 5):** `request_evidence` returns tool requests as structured JSON (no provider tool calling). `phrase_clarification` and `propose_strategies` are not used in Phase 5.

| Task | Input | Output | Deterministic fallback |
|---|---|---|---|
| `prioritize_hypotheses` | incident summary, candidate templates, evidence summaries | ordering + rationale claims | catalog order × applicability score |
| `request_evidence` (tool calling) | hypothesis, evidence so far, tool schemas | tool calls (allowlist only) | the template's required tools only |
| `phrase_clarification` | criterion, question_spec, evidence refs | question text + why-asking | template text from `question_spec` |
| `synthesize_findings` | incidents, hypotheses with statuses, evidence, human inputs | narrative claims with placeholders | template narrative per status |
| `propose_strategies` | root causes, constraints, strategy catalog | strategy selections + params | catalog mapping hypothesis → strategy |

### 8.3 Output validation pipeline (FR-16, SEC-12)

1. Parse JSON against the schema, then Pydantic strict validation.
2. **Referential check:** every ID (evidence, hypothesis, incident, template, strategy, tool) exists in this investigation, catalog or allowlist.
3. **Placeholder contract:** free-text fields may contain digits and entity names only inside `{{ev:<evidence_id>.<field>}}` or `{{entity:<type>:<id>}}`. The regex check rejects any other digit (`[0-9]`) and any exact match of a known entity name. The server renders placeholders from stored evidence and entity tables using locale formatting.
4. **Constraint check:** strategy parameters are within human constraints (§10.3).
5. On failure: one repair attempt with the validation errors, then the deterministic fallback. `llm.fallback_used` is recorded.

### 8.4 Data minimisation and injection resistance

The LLM receives aggregates and evidence results only: no raw rows, no customer IDs (customer-level evidence is passed as counts and shares; IDs stay server-side), no secrets. Untrusted text (`reason_text` samples, human free text) is passed in a dedicated JSON field `untrusted_text`, with a system instruction that it is data. Its content cannot change the output schema or the allowlisted tools, so injection impact is bounded to wording inside validated fields.

## 9. Investigation Replay (FR-17, FR-18, ADR-008)

### 9.1 Event log

`investigation_events` is append-only (DATA_CONTRACTS §8). Each domain change writes its event **in the same transaction**. `seq` is per investigation, gap-free, assigned under a row lock on the investigation (`SELECT … FOR UPDATE` on `investigations` and then `last_seq + 1`).

### 9.2 Projection

`project(events[0..n]) -> InvestigationView` is a pure reducer in `investigation/events.py`. It is used both for live state (`GET /investigations/{id}` = projection at the latest seq, plus current statuses) and for replay (`?at_seq=n`). Using one reducer guarantees replay fidelity (T-29). Events carry the data needed to project (IDs, statuses, confidences, rendered-claim references), and large results are referenced by evidence ID.

### 9.3 Human-influence view

> **Amended by AMD-15 (Phase 6):** implemented as stored interpretation versions (one per answer) and a pure snapshot diff (`GET …/versions/compare`), showing changed findings, hypotheses, root causes and recommendations and whether every earlier measurement is preserved. Event-sequence projection remains Phase 7.

For each human event `h`, the UI shows `project(seq_h − 1)` against `project(next stable seq after h's effects)`. Stable seq means the next `awaiting_human` or terminal event. The view highlights changed hypothesis statuses and confidences, new revisions and impact deltas, plan changes, and the causal chain (`caused_by_event_id`).

### 9.4 Revision compare

`GET /investigations/{id}/revisions/compare?from=&to=` diffs incidents by key (added, removed, changed impact or z), evidence by `(tool, params)` (hash changed?), and hypotheses (status and confidence).

### 9.5 Re-verify

`POST /investigations/{id}/revisions/{rid}/reverify` re-executes evidence tools for a revision. For each item it reports `match` or `mismatch` against the stored `result_hash`. Mismatches are stored as events and never overwrite evidence.

## 10. Recovery planning and execution (FR-19 to FR-24)

### 10.1 Constraints and assumptions (human-provided)

Constraints: `max_discount_pct` (0–50), `budget_amount` (≥ 0), `excluded_channels[]`, `excluded_regions[]`, `allowed_action_types[]`, `horizon_weeks` (1–12). Assumptions are per strategy, entered as low, mid and high values in bounded ranges, prefilled from `analysis_config` defaults and labelled as assumptions. They are stored with the plan, and the UI never shows them as facts.

### 10.2 Strategy catalog v1

| Strategy | Addresses | Actions it can produce | Required assumptions |
|---|---|---|---|
| `S_QUALITY_HOLD` | H_RETURNS_QUALITY | tracker task, Slack alert | `effectiveness` (share of excess refunds avoided) |
| `S_FULFILLMENT_ESCALATION` | H_FULFILLMENT | tracker task, Slack alert | `effectiveness` |
| `S_WINBACK` | H_CUSTOMER_CHURN | customer list export, tracker task | `response_rate`, uses `max_discount_pct` |
| `S_DISCOUNT_GUARDRAIL` | H_PRICE_DISCOUNT | tracker task | `compliance_rate` |
| `S_MIX_REBALANCE` | H_MIX_SHIFT | tracker task | `mix_recovery_share` |
| `S_KEY_ACCOUNT_RECOVERY` | H_CONCENTRATION | customer list export (top-k), tracker task | `recovery_probability` |
| `S_CHANNEL_REVIEW` | H_CHANNEL | tracker task | `effectiveness` |
| `S_DATA_FIX` | H_DATA_QUALITY | tracker task | none (impact not estimated) |
| `S_CONTEXT_FOLLOWUP` | H_EXTERNAL_CONTEXT | tracker task | none |

Every plan also gets one `sheets.write_recovery_plan` action (the plan summary) and optionally one `slack.post_summary`.

### 10.3 Impact estimator (deterministic)

The estimator computes `low`, `mid` and `high` for each assumption scenario over `horizon_weeks = h`. Weekly excess is taken from the incident's evidence (impact / n_W):

- `S_QUALITY_HOLD`, `S_FULFILLMENT_ESCALATION`, `S_CHANNEL_REVIEW`: `weekly_excess × h × effectiveness`
- `S_WINBACK`: `n_targets × response_rate × net_revenue_per_active_customer_week_B × h − n_targets × response_rate × aov_B × max_discount_pct`. `n_targets` = the count of customers in the targeted segments (computed).
- `S_DISCOUNT_GUARDRAIL`: `(discount_rate_W − discount_rate_B) × weekly_gross_sales_W × h × compliance_rate`
- `S_MIX_REBALANCE`: `weekly_mix_effect × h × mix_recovery_share` (mix effect taken from `mix_rate_decomposition`)
- `S_KEY_ACCOUNT_RECOVERY`: `Σ top-k customer lost weekly net revenue × h × recovery_probability`

The plan validator rejects plans where Σ estimated discount cost > `budget_amount`, where an action targets an excluded channel or region, where the action type is not allowed, or where a discount exceeds `max_discount_pct`.

### 10.4 Action review (FR-20, ADR-009)

- An action payload is canonical JSON (sorted keys, normalised decimals). `payload_hash = sha256(payload)`.
- `PATCH /actions/{id}` is allowed only in `proposed`. It re-validates against constraints and recomputes the hash.
- `POST /actions/{id}/approve {payload_hash}`: owner only. The hash must equal the current one (else 409 `stale_payload`). The action must be `proposed` and the integration it needs must be connected and verified. It creates an `approvals` row (user, hash, timestamp) and enqueues `execute_action` (dedupe key `action_id:payload_hash`) in one transaction.
- `POST /actions/{id}/reject {reason}`: owner or analyst. Terminal. No job and no execution record (SEC-13).
- `POST /actions/{id}/revoke`: owner, only while `approved` or `queued` and not yet executing. Cancels the job.

### 10.5 Execution and idempotency

> **Amended by AMD-18 (Phase 8):** one owner-requested action `sheets.write_recovery_tasks` per approved plan version, with a stable uuid5 ID. Statuses `pending → succeeded → verified`, `failed`, `manual_review`. Lease on the action, re-authorization before every write, reconcile-by-read before every write, read-back verification. See ROOT_MASTER_PLAN §13 AMD-18.

Action state machine:

```
proposed ─► approved ─► queued ─► executing ─┬─► succeeded
   │  ▲                                      ├─► failed ──(owner retry)──► queued
   │  └─(edit after approval invalidates: approval revoked)│
   ├─► rejected (terminal)                   └─► outcome_unknown ─(owner: reconcile | retry | mark_failed)
   └─► revoked (from approved/queued)
```

Executor steps for `execute_action`:

1. Load the action under `FOR UPDATE`. Abort with no side effect unless all of these hold: status ∈ {`approved`, `queued`} (or `failed` with an owner retry request); the latest approval's hash = the current `payload_hash`; the approver is still an `owner` member of the business; the integration is still verified; the action's `business_id` equals the job's.
2. Insert an `action_executions` row (attempt N, `started`) and set the action to `executing`. **Commit before the external call.**
3. Call the adapter with `idempotency_key = sha256(action_id + payload_hash)`.
4. Record the outcome:
   - success → `succeeded` with the external reference (tab name or ID, row range, Slack timestamp if available).
   - transient error (timeout, 429, 5xx) → reconcile (Sheets only, §11.4). If not found, schedule a retry within `max_attempts`.
   - permanent error (403, 404, validation) → `failed` with the error code.
   - ambiguous with no reconcile possible (Slack timeout after send) → `outcome_unknown`.
5. Write audit and event rows in the same transaction as the outcome.

Lease expiry during step 3 → the job is re-leased → step 1 finds `executing` with an attempt `started` → reconcile first (Sheets) or mark `outcome_unknown` (Slack). The external write is never repeated blindly.

## 11. Integrations

> **Amended by AMD-18 (Phase 8):** Google Sheets only (Slack not delivered). `google-auth` signs the service-account JWT and `httpx` calls the Sheets REST API directly. Tabs `ROOT Review Tasks` and `ROOT Service Queue` with a key column replace the §11.3 action types. Global spreadsheet uniqueness covers verified bindings only.

> **Amended by AMD-19 (Phase 8):** alternatively, an Apps Script web app inside the business's spreadsheet (`integrations/apps_script/Code.gs`) reached at `SHEETS_BRIDGE_URL` with `SHEETS_BRIDGE_TOKEN`; same executor, idempotency and verification.

### 11.1 Google Sheets model (ADR-012)

- One Google Cloud **service account** for the deployment. Its JSON key is in the env var `GOOGLE_SERVICE_ACCOUNT_JSON` (base64), backend only. Scope: `https://www.googleapis.com/auth/spreadsheets`. Library: the official Google API Python client with `google-auth`.
- The service account can only reach spreadsheets explicitly shared with its email address.

### 11.2 Connection and ownership verification (FR-22, SEC-17)

1. An owner submits a spreadsheet URL or ID. The server extracts and validates the ID (`^[A-Za-z0-9_-]{20,100}$`).
2. The server generates a verification code (`ROOT-` + 10 random base32 characters, valid 30 min, stored hashed) and returns it with the service-account email.
3. The owner shares the sheet with the service-account email (Editor) and types the code into cell `A1` of a tab named `ROOT_VERIFY`.
4. `POST …/verify`: the server reads `ROOT_VERIFY!A1` and compares it in constant time. On success the integration becomes `verified`, the code is consumed, and the server attempts to clear A1. A **unique constraint on `spreadsheet_id`** across all businesses prevents binding a sheet to two tenants.
5. Disconnect sets `revoked`. Pending approved actions that use the integration fail pre-execution checks.

### 11.3 Write behaviour

| Action type | Write | Idempotency marker |
|---|---|---|
| `sheets.write_recovery_plan` | Create a new tab `ROOT Plan <short_action_id>` with header + rows (strategy, target, rationale rendered, estimates low/mid/high, assumptions, approver, approved_at) | The tab title contains `short_action_id`. Cell `Z1` holds the idempotency key |
| `sheets.write_customer_list` | Create a new tab `ROOT Customers <short_action_id>` with customer_id, segment, recency_days, orders_365, net_revenue_365, flags | same |
| `sheets.append_task_tracker` | Ensure a tab `ROOT Tasks` exists (header row). Append rows (task_key, strategy, description, owner_hint, due_week, status=`open`, action_id) | `task_key = <idempotency_key>:<n>`. Before appending, read the `task_key` column and skip existing keys |

All writes use `valueInputOption=RAW`. Every string cell is escaped (SEC-16): a leading `= + - @`, tab or CR is prefixed with `'`. Cell text is capped at 5,000 characters. Row count per action is ≤ 5,000 (customer lists are truncated by value rank, and truncation is noted in the tab).

### 11.4 Errors, rate limits and reconciliation

Exponential backoff on 429/5xx (max 5 attempts per job run, honouring `Retry-After`). Reconciliation reads spreadsheet metadata for a tab with the idempotency key (or reads `ROOT Tasks` keys). If found, the action is marked `succeeded` without writing. Permissions errors (403) → `failed` with `integration_permission_denied`, and the owner is told to re-share.

### 11.5 Slack (optional, FR-23)

- An incoming webhook URL must match `^https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+$` (SEC-15). It is stored encrypted (AES-256-GCM, key `INTEGRATION_ENCRYPTION_KEY`, with a key ID for rotation) and never returned or logged.
- `slack.post_summary` posts plain text plus blocks without user-controlled mrkdwn links (text escaped for `& < >`). Timeout 10 s. Non-idempotent: a timeout or connection reset after the request was sent → `outcome_unknown`, and the owner decides. A test message endpoint is owner-only and rate-limited.

## 12. LangGraph orchestration (ADR-006)

### 12.1 State schema

```python
class HypothesisRef(TypedDict):
    hypothesis_id: str
    template: str
    status: Literal["pending", "supported", "refuted", "inconclusive", "needs_human_context"]
    rejected_by_human: bool
    confidence: float | None

class InvestigationState(TypedDict):
    investigation_id: str
    business_id: str
    dataset_id: str
    revision_id: str                 # current analysis revision
    overlay_version: int
    config_version: int
    phase: str
    incident_queue: list[str]        # incident ids to investigate, ranked
    current_incident_id: str | None
    hypotheses: dict[str, list[HypothesisRef]]   # incident_id -> refs
    clarifications_asked: dict[str, int]          # incident_id -> count
    challenge_count: int
    pending_interrupt: dict[str, str] | None      # {"kind": ..., "ref_id": ...}
    findings_id: str | None
    plan_id: str | None
    llm_mode: Literal["llm", "deterministic"]
    terminal_status: str | None
```

The state holds **IDs and small statuses only**. Domain tables are the source of truth. Nodes read the details they need.

### 12.2 Graph and transitions

> **Amended by AMD-15 (Phase 6):** graph 2.0.0: `initialize → (select_incident → generate_hypotheses → select_tools → retrieve_evidence → verify_evidence)* → draft_findings → assess_context → await_clarification [interrupt] → apply_answer → (affected incidents again) → draft_findings → … → finalize`.

> **Amended by AMD-12 (Phase 5):** the Phase 5 graph has no interrupts: `initialize → select_incident → generate_hypotheses → select_tools → retrieve_evidence → verify_evidence → detect_missing_context → … → synthesize_output → finalize`.

```
START → load_context → compute_metrics → detect_incidents
detect_incidents ─(no incidents)→ finalize[completed_no_incidents]
                 ─(insufficient)→ finalize[insufficient_data]
                 ─(incidents)  → select_incident
select_incident ─(queue empty)→ synthesize_findings
                ─(next)       → generate_hypotheses → gather_evidence → verify_hypotheses → assess_human_need
assess_human_need ─(trigger & under cap)→ await_clarification → apply_human_input
                  ─(else)               → select_incident
apply_human_input ─(correction)  → compute_metrics   (new revision; incident queue rebuilt, already-answered clarifications are not re-asked)
                  ─(context)     → verify_hypotheses
                  ─(rejection)   → verify_hypotheses
                  ─(skip)        → select_incident
synthesize_findings → await_findings_review
await_findings_review ─(accept)              → await_plan_constraints
                      ─(challenge & count<2) → generate_hypotheses (challenged incident)
                      ─(challenge & count=2) → await_plan_constraints (findings marked disputed)
await_plan_constraints → plan_recovery → await_action_decisions → finalize[completed]
any node ─(unhandled exception)→ error handler: investigation failed (recoverable), checkpoint retained
```

### 12.3 Nodes

| Node | Kind | Writes |
|---|---|---|
| `load_context` | deterministic | validates the dataset is `ready`, creates or loads the revision |
| `compute_metrics` | deterministic | `metric_results`, `customer_segments` for the revision (upsert by revision) |
| `detect_incidents` | deterministic | `incidents` (upsert by revision + key) |
| `select_incident` | deterministic | state only |
| `generate_hypotheses` | LLM + fallback | `hypotheses` (upsert by incident + template) |
| `gather_evidence` | deterministic tools, LLM tool selection + fallback | `evidence_items` (unique by revision + tool + params hash) |
| `verify_hypotheses` | deterministic | hypothesis status and confidence (new `hypothesis_evaluations` row per evaluation) |
| `assess_human_need` | deterministic + LLM phrasing | `clarifications` (unique by incident + criterion) |
| `await_*` | `interrupt()` | none before the interrupt (reads only) |
| `apply_human_input` | deterministic | overlay or revision rows, marks the input `applied` |
| `synthesize_findings` | LLM + fallback | `findings` (version per review round) |
| `plan_recovery` | LLM + fallback + validator + estimator | `recovery_plans`, `recovery_actions` |
| `finalize` | deterministic | terminal status |

### 12.4 Checkpointing

> **Amended by AMD-13 (Phase 5 completion audit):** a session advisory lock on the checkpoint connection serializes each investigation across workers. Checkpoint loading, lock acquisition and graph execution share a configurable 600-second whole-run timeout (`workflow_timeout` on expiry), independent of the LLM deadline. The Phase 5 graph has no human interrupts; its acceptance target is structured completion from uploaded data in deterministic and fake-provider modes, with a separate opt-in live test.

- `langgraph-checkpoint-postgres` `AsyncPostgresSaver` on the same database, in a dedicated schema `langgraph` (its tables are created by its `setup()` during migration). `thread_id = investigation_id`.
- Checkpoints contain IDs only (no secrets or PII). RLS does not apply to the checkpoint schema. Access is only through the worker with a thread ID loaded from a tenant-scoped investigation row.
- Resume: `graph.ainvoke(Command(resume={"ref_id": ...}), config={"configurable": {"thread_id": id}})`.

### 12.5 Node idempotency rules

Writes use deterministic natural keys with `ON CONFLICT DO NOTHING` (or an upsert of identical values). Events are written only when the state actually changes (checked in the same transaction). A node either commits all its domain writes plus its events or none of them.

## 13. Technology stack

### 13.1 Dependencies (exact versions pinned in Phase 2, ADR-015)

| Area | Runtime dependencies | Purpose |
|---|---|---|
| Backend | Python ≥ 3.12, `fastapi`, `uvicorn`, `pydantic` v2, `pydantic-settings`, `sqlalchemy` 2.x (asyncio), `psycopg[binary,pool]` 3.x, `alembic`, `pandas`, `langgraph`, `langgraph-checkpoint-postgres`, `argon2-cffi`, `cryptography`, `google-api-python-client`, `google-auth`, `httpx`, LLM provider official SDK (Phase 7) | — |
| Backend dev | `pytest`, `pytest-asyncio`, `hypothesis`, `ruff`, `mypy`, `pip-audit`, `respx` (HTTP mocking for adapters) | — |
| Frontend | Next.js (App Router), React, TypeScript, Tailwind CSS, shadcn/ui (with its Radix peer deps), Recharts, `zod`, `react-hook-form` (shadcn Form) | — |
| Frontend dev | ESLint, Vitest, Testing Library, Playwright, `@axe-core/playwright`, `openapi-typescript` | — |
| Infra | PostgreSQL ≥ 16, Docker Compose (local DB only), GitHub Actions, gitleaks | — |

A dependency enters only in the phase that first uses it (AGENTS.md R-8).

## 14. Deployment architecture and rollback

### 14.1 Topology

| Environment | Frontend | Backend | Database |
|---|---|---|---|
| local | `next dev` | `uvicorn --reload` | Docker Compose Postgres |
| CI | built and served for Playwright | uvicorn | GitHub Actions Postgres service |
| production | Vercel project (root `frontend/`) | Railway service (root `backend/`, 1 instance) | Railway managed PostgreSQL (daily backups) |

Render (web service + managed Postgres) is the documented equivalent if Railway is unavailable. The env var names are the same.

### 14.2 Configuration

Backend env: `DATABASE_URL`, `DATABASE_URL_MIGRATIONS` (owner role), `SESSION_SECRET`, `INTEGRATION_ENCRYPTION_KEY`, `INTEGRATION_ENCRYPTION_KEY_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_MAX_CALLS`, `LLM_MAX_TOKENS`, `FRONTEND_ORIGIN`, `WORKER_CONCURRENCY`, `LOG_LEVEL`, `ENVIRONMENT`. Frontend env: `BACKEND_URL` (server-only, used by rewrites). No secret is ever prefixed `NEXT_PUBLIC_`.

### 14.3 Release process

1. CI is green on `main`.
2. Pre-deploy: `pg_dump` snapshot (or a platform backup trigger) → `alembic upgrade head` using the migration role.
3. Deploy the backend and wait for `/readyz`.
4. Deploy the frontend.
5. Smoke test (T-53).

Migrations follow **expand → migrate → contract**: a release only adds compatible schema, and destructive changes ship one release later. The previous backend build therefore runs against the new schema.

### 14.4 Rollback

- **Application:** redeploy the previous Railway deployment and Vercel deployment (instant rollback). No DB downgrade is needed because of expand/contract.
- **Data emergency:** restore the pre-deploy snapshot into a new database, switch `DATABASE_URL`, and verify readiness.
- **In-flight work:** jobs are lease-based, so restarts are safe. LangGraph checkpoints remain compatible because state schema changes are additive only. A breaking graph change needs a new graph version and lets old investigations finish on the old version (`investigations.graph_version`).
- Rollback is rehearsed in Phase 13 (AC-13.2).

## 15. Architecture decision records

**ADR-001 — Modular monolith: one backend service, one frontend app.**
*Context:* competition scope and the requirement that analysis be reproducible and debuggable. *Decision:* one FastAPI deployable containing the API, worker, graph runtime and adapters, plus one Next.js app. *Consequences:* simple deployment and transactions spanning all domain state. Worker and API share the process, so CPU-heavy analysis can reduce API responsiveness; this is mitigated by `WORKER_CONCURRENCY` and running pandas work in a thread pool. *Rejected:* microservices and a separate worker service (constraint NG-07).

**ADR-002 — PostgreSQL as the only durable store.**
*Decision:* domain data, raw uploads (bytea, ≤ 25 MB per file, purged after 30 days), jobs, events, audit and checkpoints all live in PostgreSQL. *Consequences:* one backup and restore story, transactional consistency. *Rejected:* object storage, Redis or Kafka (no demonstrated need at the target scale, NFR-02).

**ADR-003 — Postgres-backed job queue with an in-process worker.**
*Decision:* a `jobs` table with `FOR UPDATE SKIP LOCKED`, leases, heartbeats and dedupe keys. *Consequences:* atomic enqueue with domain changes (no dual-write). Throughput is bounded but sufficient (≪ 100 jobs/min). *Rejected:* Celery/RQ (needs a broker) and FastAPI BackgroundTasks (not durable).

**ADR-004 — Deterministic computation, LLM interpretation only.**
*Decision:* metrics, detection, evidence and verification are deterministic code. The LLM orders, selects and phrases. LLM output may not contain digits or entity names outside placeholders, and the server renders values from evidence. *Consequences:* every number is reproducible and the LLM cannot fabricate financial facts. Narrative style is slightly constrained.

**ADR-005 — Money in SQL NUMERIC, statistics in pandas.**
*Decision:* monetary sums and ratios that reach users are computed with `NUMERIC(18,4)` in SQL and handled as Python `Decimal`. pandas (float64) is used only for robust statistics (median, MAD, z) and reshaping. Statistical outputs are labelled as statistics, not money. *Consequences:* exact money, fast statistics.

**ADR-006 — LangGraph with the PostgreSQL checkpointer, state as references.**
*Decision:* the graph state holds IDs and statuses, and domain tables are the source of truth. Interrupts are used for all human checkpoints. *Consequences:* checkpoints stay small and contain no PII. Domain data can be queried and secured with RLS. Nodes must be idempotent.

**ADR-007 — Immutable datasets plus overlays plus analysis revisions.**
*Decision:* uploads are immutable versions. Human corrections are overlay rules. Each `(dataset, overlay, config, code)` tuple is a revision. *Consequences:* human feedback never silently rewrites evidence, and revisions can be compared and reproduced.

**ADR-008 — Append-only event log, replay as projection.**
*Decision:* every state change emits an event in the same transaction, and replay folds events with the same reducer as the live view. *Consequences:* faithful replay without re-running analysis or the LLM.

**ADR-009 — Action execution outside the agent graph, approval bound to payload hash.**
*Decision:* the graph can only *propose* actions. Execution is a separate job triggered solely by an owner approval of a specific payload hash, with authorization re-checked before every external call. *Consequences:* LLM output can never cause a side effect directly, and rejected actions have no execution path.

**ADR-010 — Server-side sessions through a same-origin proxy.**
*Decision:* the backend issues an opaque session cookie (HttpOnly, Secure, SameSite=Lax). The frontend proxies `/api/*` to the backend with Next.js rewrites, so cookies are first-party. *Consequences:* instant revocation, no tokens in JS, CSRF still mitigated (SEC-03). *Rejected:* JWT in localStorage (XSS exposure, no revocation).

**ADR-011 — Tenant isolation with application scoping plus Postgres RLS.**
*Decision:* explicit `business_id` filters in repositories and RLS policies keyed on a transaction-local setting, with the app connecting as a non-owner role. *Consequences:* defence in depth against IDOR and query bugs.

**ADR-012 — Google Sheets through a service account with ownership verification.**
*Decision:* a service account instead of per-user OAuth. Tenants prove control with a verification code. *Consequences:* no user refresh tokens to store, minimal scopes. Tenants must share sheets manually (documented UX step).

**ADR-013 — Cursor-based polling for live progress.**
*Decision:* `GET /investigations/{id}/events?after_seq=` every 2 s while active. *Consequences:* works through the Vercel rewrite and serverless limits, and reuses the replay event model. *Rejected:* WebSockets and SSE through the proxy (connection-duration limits, extra complexity).

**ADR-014 — Bounded catalogs for hypotheses and strategies.**
*Decision:* the LLM chooses among versioned templates with explicit verification criteria and estimators. *Consequences:* every claim is testable, which trades open-ended creativity for verifiability. Human context (`H_EXTERNAL_CONTEXT`) covers causes outside the catalog.

**ADR-015 — Data access and dependency stack.**
*Decision:* SQLAlchemy 2.x (ORM for entities, Core/`text()` with bound parameters for analytics), Alembic migrations, psycopg 3 (shared with the LangGraph checkpointer), and `uv` for locked Python dependencies. Exact versions are pinned against current registries in Phase 2. *Consequences:* mainstream, type-friendly stack, with no second database driver.

**ADR-016 — Single LLM provider behind a gateway, deterministic fallback.**
*Decision:* one provider adapter, chosen in Phase 7 after verifying structured-output and tool-calling support in its current documentation (recorded as an amendment). Every LLM task has a deterministic fallback. *Consequences:* the provider can be swapped by implementing one adapter, and investigations survive provider outages (NFR-03).

**ADR-017 — Analysis anchored to the dataset as-of date.**
*Decision:* windows are relative to `T = max(order_date)`, never to wall-clock time. *Consequences:* reproducible results regardless of when the analysis runs.

**ADR-018 — No direct PII ingestion.**
*Decision:* the customer contract has pseudonymous IDs only. PII-like columns are dropped before storage. *Consequences:* smaller breach impact and safe LLM inputs. Outreach exports use IDs that the business maps in its own CRM.

**ADR-019 — ISO-week granularity, complete weeks only.**
*Decision:* detection runs on complete ISO weeks. *Consequences:* removes partial-week and day-of-week artefacts, at the cost of up to 6 days of detection latency.

## 16. Performance and scalability

- **Target scale:** ≤ 500k sales lines, 50k customers, 5k products and 100 distinct values per dimension for each dataset. Tens of concurrent users.
- **Ingestion:** streaming CSV parse in chunks of 50k rows, `COPY` into staging tables, set-based validation in SQL, then insert into final tables.
- **Indexes:** `(business_id, dataset_id, order_date)`, `(business_id, dataset_id, product_id)`, `(business_id, dataset_id, customer_id)`, `returns(business_id, dataset_id, return_date)`, plus `(investigation_id, seq)` on events.
- **Analytics:** weekly aggregates per revision are materialised into `metric_results` once and reused by detection and evidence tools.
- Evidence results are capped in size (≤ 256 KB JSON).
- CPU-bound pandas runs via `asyncio.to_thread`.
- **Deferred until measured (no premature optimisation):** partitioning, read replicas, caching layers.

## 17. Known architectural risks

| Risk | Mitigation |
|---|---|
| Worker in the API process contends for CPU | Concurrency limit, thread offload, measured in Phase 12. A separate process of the *same* image is the escape hatch, requiring an amendment |
| Multiple-comparison false positives in detection | Materiality, minimum support, robust z, calibration in Phase 6 (AC-6.1) |
| Static product cost (A4) can misattribute margin changes | Stated in the UI. `H_MIX_SHIFT` and `H_PRICE_DISCOUNT` remain testable |
| Sheets sharing step adds demo friction | Pre-connected demo business. Verification is shown in a rehearsal video |
| Slack non-idempotency | `outcome_unknown` with human decision. Slack is optional |
| LangGraph API changes | Pin versions, keep graph code thin, contract tests around resume |
