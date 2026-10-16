# ROOT 2.0 — Data Contracts

> Baseline created in Phase 1. This document is authoritative for data shapes, formulas, API and LLM contracts.
> Any change must be versioned (an `analysis_config` version, `api` version, or catalog version) and recorded as an amendment in [ROOT_MASTER_PLAN.md §13](ROOT_MASTER_PLAN.md).

## 1. Conventions

- **Identifiers:** internal IDs are UUIDv7 (`uuid` type) and are exposed as strings. Business-supplied IDs (`order_id`, `product_id`, …) are strings matching `^[A-Za-z0-9._:\-]{1,64}$`.
- **Money:** `NUMERIC(18,4)` in the DB, `Decimal` in Python, and **decimal strings** in JSON (`"1234.5600"`), never floats. Display rounding is half-even to 2 dp, at presentation only.
- **Rates:** `NUMERIC(9,6)` fractions in [0, 1] (or unbounded where stated), decimal strings in JSON. The UI shows them as percentages.
- **Dates:** calendar dates `YYYY-MM-DD` without time zone. Timestamps are `timestamptz` in UTC, ISO 8601 in JSON.
- **Weeks:** ISO 8601 weeks, Monday start, labelled `YYYY-Www`.
- **Canonical JSON** (for hashing): UTF-8, keys sorted, no insignificant whitespace, decimals normalised (no exponent, trailing zeros stripped), arrays in a defined order.
- **Null semantics:** `null` means undefined (for example a zero denominator). It is never coerced to `0`.

## 2. Upload contracts (CSV)

One dataset upload consists of exactly four files: `products.csv`, `customers.csv`, `sales.csv`, `returns.csv`. `returns.csv` may contain a header only.

### 2.1 `products.csv`

| Column | Type | Required | Rules |
|---|---|---|---|
| `product_id` | id | yes | unique |
| `product_name` | text ≤ 200 | yes | control characters stripped |
| `category` | dimension text ≤ 64 | yes | normalised: trimmed, lowercase |
| `unit_cost` | decimal ≥ 0 | yes | ≤ 4 dp |
| `list_price` | decimal > 0 | yes | ≤ 4 dp |

### 2.2 `customers.csv`

| Column | Type | Required | Rules |
|---|---|---|---|
| `customer_id` | id | yes | unique, pseudonymous |
| `signup_date` | date | no | ≥ 2000-01-01, ≤ upload date + 1 day |
| `region` | dimension text ≤ 64 | no | normalised |
| `acquisition_channel` | dimension text ≤ 64 | no | normalised |

### 2.3 `sales.csv` (one row per order line)

| Column | Type | Required | Rules |
|---|---|---|---|
| `order_line_id` | id | yes | unique |
| `order_id` | id | yes | — |
| `order_date` | date | yes | ≥ 2000-01-01, ≤ upload date + 1 day. All lines of one `order_id` share `order_date` and `customer_id` |
| `customer_id` | id | yes | must exist in customers |
| `product_id` | id | yes | must exist in products |
| `quantity` | integer | yes | 1 … 100000 |
| `unit_price` | decimal ≥ 0 | yes | ≤ 4 dp, ≤ 1e9 |
| `discount_amount` | decimal ≥ 0 | no (default 0) | ≤ quantity × unit_price |
| `channel` | dimension text ≤ 64 | no | normalised. Missing → `unknown` |
| `region` | dimension text ≤ 64 | no | normalised. Missing → `unknown` |

### 2.4 `returns.csv`

| Column | Type | Required | Rules |
|---|---|---|---|
| `return_id` | id | yes | unique |
| `order_line_id` | id | yes | must exist in valid sales lines |
| `return_date` | date | yes | ≥ the line's `order_date`, ≤ upload date + 1 day |
| `quantity_returned` | integer ≥ 1 | yes | Σ per line ≤ line quantity |
| `refund_amount` | decimal ≥ 0 | yes | Σ per line ≤ line net value (qty × unit_price − discount_amount) |
| `reason_code` | enum | yes | `damaged`, `defective`, `wrong_item`, `not_as_described`, `late_delivery`, `changed_mind`, `other` |
| `reason_text` | text ≤ 500 | no | untrusted, control characters stripped, never interpreted |

### 2.5 File-level rules

- Encoding UTF-8 (a BOM is allowed). RFC 4180 CSV, comma delimiter, `"` quoting, header row required.
- Header matching is case-insensitive and whitespace-trimmed. Column order is free.
- **Size limits:** ≤ 25 MB per file, ≤ 60 MB per upload. Rows: sales ≤ 500,000, customers ≤ 100,000, products ≤ 10,000, returns ≤ 200,000.
- Decimal separator `.`. No thousands separators or currency symbols.
- Each dimension (`category`, `region`, `channel`, `acquisition_channel`) has ≤ 100 distinct values.
- **Unknown columns** are ignored with a warning.
- **PII-suspect columns** are dropped *before* storage with a warning: any header that, normalised, matches `email|e_mail|phone|mobile|name|first_name|last_name|full_name|address|street|postcode|zip|ssn|dob|birth|ip|card` (except `product_name`).
- The currency is set per business (ISO 4217) and is not a column (assumption A1).

### 2.6 Validation rules

Severity: **E-file** rejects the dataset. **E-row** quarantines the row. **W** is a warning. If quarantined rows exceed **5%** of any file (`V-099`), the dataset is rejected.

| Code | Severity | Rule |
|---|---|---|
| V-001 | E-file | Missing one of the four files |
| V-002 | E-file | File not decodable as UTF-8, or contains a NUL byte |
| V-003 | E-file | Size or row limit exceeded |
| V-004 | E-file | Required column missing |
| V-005 | E-file | Duplicate header column |
| V-006 | E-file | Dimension cardinality > 100 |
| V-007 | E-file | Fewer than 1 valid sales row |
| V-010 | E-row | Malformed CSV row (field count mismatch) |
| V-011 | E-row | Required value empty |
| V-012 | E-row | ID format invalid |
| V-013 | E-row | Date invalid or out of range |
| V-014 | E-row | Number invalid, negative where not allowed, > 4 dp, or out of range |
| V-015 | E-row | Text exceeds max length |
| V-016 | E-row | `reason_code` not in enum |
| V-020 | E-row | Conflicting duplicate primary key (same key, different values): **all** rows with that key are quarantined |
| V-021 | W | Exact duplicate rows: one is kept |
| V-030 | E-row | `sales.customer_id` not found in valid customers |
| V-031 | E-row | `sales.product_id` not found in valid products |
| V-032 | E-row | `returns.order_line_id` not found in valid sales lines |
| V-033 | E-row | `discount_amount` > quantity × unit_price |
| V-034 | E-row | `return_date` < `order_date` |
| V-035 | E-row | Cumulative `quantity_returned` > line quantity (returns that would exceed it, in `return_date`, `return_id` order) |
| V-036 | E-row | Cumulative `refund_amount` > line net value (same ordering) |
| V-037 | E-row | Lines of one `order_id` disagree on `order_date` or `customer_id`: all lines of the order are quarantined |
| V-040 | W | Unknown column ignored |
| V-041 | W | PII-suspect column dropped |
| V-042 | W | Sales history < 12 complete ISO weeks (detection will report `insufficient_data`) |
| V-043 | W | `unit_price` > 3 × `list_price` or < 0.1 × `list_price` |
| V-099 | E-file | Quarantined rows > 5% of a file |

Validation is deterministic. Issues are stored with file, row number (1-based, header = row 1), column, code and a sanitised value excerpt (≤ 100 chars). The report lists counts per code plus the first 1,000 issues, and the full list is paginated.

## 3. Financial metric definitions

### 3.1 Accounting assumptions

| ID | Assumption |
|---|---|
| A1 | One currency per business. No FX |
| A2 | Sales are recognised on `order_date`. Tax and shipping are out of scope |
| A3 | Refunds are recognised on `return_date` (period view). The cohort view attributes a refund to the order line's `order_date` |
| A4 | `unit_cost` is constant across the dataset period |
| A5 | Returned units with reason `damaged` or `defective` are written off. Units returned for other reasons are restocked (their COGS is reversed) |
| A6 | `discount_amount` is line-level. Order-level discounts must be allocated to lines before upload |
| A7 | Partial refunds are allowed. Σ refunds per line ≤ line net value (V-036) |
| A8 | Gross profit excludes operating, shipping and marketing costs |
| A9 | All sums use exact decimal arithmetic. Ratios use unrounded sums |
| A10 | Ratios with zero (or, where stated, non-positive) denominators are `null` |
| A11 | A ratio over several periods is Σ numerator / Σ denominator, never the mean of ratios |

### 3.2 Base sets

For a period P (a week, window or range) and slice S (dimension = value, or overall), over the **effective data** of a revision (dataset + overlay, §5):

- `L(P,S)` = valid sales lines with `order_date ∈ P` matching S.
- `R(P,S)` = valid returns with `return_date ∈ P` whose sales line matches S.
- Slice matching uses the line's `product_id → category`, `region`, `channel`, `customer_id → segment`. Returns inherit the slice of their line.

### 3.3 Metrics

| # | Metric | Formula | Period basis | Undefined when |
|---|---|---|---|---|
| M01 | `gross_sales` | Σ_{L} quantity × unit_price | order_date | never (0 if empty) |
| M02 | `discounts` | Σ_{L} discount_amount | order_date | never |
| M03 | `net_sales` | gross_sales − discounts | order_date | never |
| M04 | `refunds` | Σ_{R} refund_amount | return_date | never |
| M05 | `net_revenue` | net_sales − refunds | mixed (M03, M04) | never |
| M06 | `cogs_sold` | Σ_{L} quantity × product.unit_cost | order_date | never |
| M07 | `cogs_restocked` | Σ_{R, reason ∉ {damaged, defective}} quantity_returned × product.unit_cost | return_date | never |
| M08 | `gross_profit` | net_revenue − cogs_sold + cogs_restocked | mixed | never |
| M09 | `gross_margin_pct` | gross_profit / net_revenue | mixed | net_revenue ≤ 0 |
| M10 | `units_sold` | Σ_{L} quantity | order_date | never |
| M11 | `units_returned` | Σ_{R} quantity_returned | return_date | never |
| M12 | `orders` | count distinct order_id in L | order_date | never |
| M13 | `aov` | net_sales / orders | order_date | orders = 0 |
| M14 | `discount_rate` | discounts / gross_sales | order_date | gross_sales = 0 |
| M15 | `refund_rate` | refunds / net_sales | mixed | net_sales = 0 |
| M16 | `unit_return_rate` | units_returned / units_sold | mixed | units_sold = 0 |
| M17 | `active_customers` | count distinct customer_id in L | order_date | never |
| M18 | `new_customers` | count of customers in M17 whose first `order_date` in the whole effective dataset ∈ P | order_date | never |
| M19 | `repeat_customer_rate` | (count of customers in M17 with an order before P's start) / active_customers | order_date | active_customers = 0 |
| M20 | `net_revenue_per_active_customer_week` | Σ_weeks net_revenue / Σ_weeks active_customers (weekly active counts summed) | per week | Σ active = 0 |
| M21 | `matured_cohort_refund_rate` | For lines with `order_date ∈ P` and `order_date ≤ T − return_maturity_days`: Σ refund_amount of their returns with `return_date ≤ order_date + return_maturity_days` / net_sales of those lines | order_date cohort | denominator = 0, or no matured lines |

Product slices for M09 use the same formula restricted to the product's lines and returns. All metrics are computed per ISO week and per requested range.

### 3.4 Dimensions (allowlist)

`overall`, `category`, `product`, `region`, `channel`, `customer_segment`, `acquisition_channel`. Any other dimension name → 422 `invalid_dimension`. SQL column expressions come from a server-side mapping and are never from input.

### 3.5 `analysis_config` v1 constants

| Key | Value | Used by |
|---|---|---|
| `current_weeks` | 4 | detection |
| `baseline_weeks` | 12 | detection |
| `min_baseline_weeks` | 8 | detection |
| `z_threshold` | 3.0 | detection |
| `rel_change_threshold` | 0.15 | detection (amounts, counts) |
| `rate_abs_threshold_pp` | 0.02 | detection (rates) |
| `rate_rel_threshold` | 0.20 | detection (rates) |
| `materiality_pct` | 0.01 | detection |
| `min_impact_amount` | 0.00 | detection |
| `min_slice_orders_baseline` | 30 | detection |
| `min_slice_orders_current` | 10 | detection |
| `min_units_current_refund_rate` | 30 | detection |
| `product_slice_coverage` | 0.80 | detection |
| `max_product_slices` | 50 | detection |
| `child_share_threshold` | 0.60 | consolidation |
| `overlap_jaccard_threshold` | 0.60 | consolidation |
| `max_incidents` | 5 | ranking |
| `max_investigated_incidents` | 3 | ranking |
| `return_maturity_days` | 14 | M21, evidence |
| `rfm_lookback_days` | 365 | segmentation |
| `new_customer_days` | 30 | segmentation |
| `churn_gap_multiplier` | 2.0 | risk flags |
| `churn_min_recency_days` | 30 | risk flags |
| `return_risk_min_rate` | 0.30 | risk flags |
| `return_risk_multiplier` | 2.0 | risk flags |
| `key_account_share` | 0.05 | risk flags |
| `low_sample_customers` | 20 | segmentation |
| `default_assumptions` | `effectiveness` {0.2, 0.4, 0.6}; `response_rate` {0.05, 0.10, 0.20}; `compliance_rate` {0.5, 0.7, 0.9}; `mix_recovery_share` {0.1, 0.25, 0.4}; `recovery_probability` {0.2, 0.4, 0.6} | planning (prefill only; the human confirms or edits) |

Config rows are immutable. A change creates v2, and existing revisions keep their version.

## 4. Customer loyalty and risk segmentation

The as-of date is `T` (ARCHITECTURE §5.1). It is computed over the effective data of a revision for customers with ≥ 1 valid order.

**Inputs per customer:**

- `recency_days` = T − last order_date
- `frequency` = distinct orders with order_date ∈ (T − 365 d, T]
- `monetary` = net_revenue of the customer's lines and returns in the same window. Refunds are attributed through the line, and `return_date ≤ T`
- `first_order_date`
- `lifetime_orders`
- `median_gap_days` (median of gaps between consecutive distinct order dates; needs ≥ 3 orders)
- `unit_return_rate_lifetime`
- `returned_lines_lifetime`

**Scores (1–5).** For each of R (goodness = −recency_days), F (frequency) and M (monetary):

`score = clamp(ceil(5 × pct), 1, 5)` with `pct = rank(goodness, method="max", ascending=True) / n`.

Ties share a score. The calculation is deterministic. If `n < 20`, the scores are still computed and the result is flagged `low_sample`.

**Segment (first match wins):**

| Order | Segment | Rule |
|---|---|---|
| 1 | `new` | first_order_date > T − 30 d |
| 2 | `champions` | R ≥ 4 ∧ F ≥ 4 ∧ M ≥ 4 |
| 3 | `loyal` | R ≥ 3 ∧ F ≥ 4 |
| 4 | `at_risk` | R ≤ 2 ∧ F ≥ 3 ∧ M ≥ 3 |
| 5 | `lost` | R = 1 ∧ F ≤ 2 |
| 6 | `hibernating` | R ≤ 2 ∧ F ≤ 2 |
| 7 | `regular` | otherwise |

**Risk flags (independent, may co-occur):**

- `churn_risk`: lifetime_orders ≥ 3 ∧ recency_days > 2.0 × median_gap_days ∧ recency_days > 30.
- `return_risk`: returned_lines_lifetime ≥ 2 ∧ unit_return_rate_lifetime ≥ max(0.30, 2.0 × business unit_return_rate over the dataset).
- `key_account`: monetary ≥ 0.05 × Σ monetary of all customers.

Stored per revision in `customer_segments`. Segments for slicing in detection are computed as of the last day of baseline B (ARCHITECTURE §5.3).

## 5. Analysis revisions and reproducibility

- **Revision key:** `(dataset_id, overlay_version, config_version, code_version)`. `code_version` is the analytics package version plus the git commit, set at build.
- **Effective data:** base rows minus `exclude_records` rules, with `reclassify_return_reason` overrides applied. `exclude_baseline_period` applies only to baseline weeks in detection and evidence.
- `result_hash` is SHA-256 of the canonical JSON of each stored result (`metric_results`, `customer_segments` as a sorted list, incident list, evidence result).
- `revision_hash` is SHA-256 of the concatenated sorted result hashes. NFR-01 requires equality on recomputation.

## 6. Database schema

All tenant-scoped tables have `business_id uuid NOT NULL REFERENCES businesses ON DELETE CASCADE`, RLS enabled and forced, and an index leading with `business_id`. `created_at timestamptz NOT NULL DEFAULT now()` is implied everywhere.

### 6.1 Identity and tenancy

| Table | Key columns | Notes |
|---|---|---|
| `users` | id, email (citext unique), password_hash, display_name, is_active, failed_login_count, locked_until | Not tenant-scoped |
| `sessions` | id_hash (pk, SHA-256 of the token), user_id, csrf_secret_hash, created_at, last_seen_at, expires_at, revoked_at, user_agent_hash, ip_prefix | Token never stored raw |
| `auth_attempts` | id, email_hash, ip_hash, succeeded, at | Throttling window queries |
| `businesses` | id, name, currency (char 3), timezone, created_by, deleted_at | The tenant root. RLS by membership |
| `memberships` | business_id, user_id, role (`owner`, `analyst`, `viewer`), invited_by | PK (business_id, user_id). At least one owner is enforced |
| `idempotency_keys` | business_id, user_id, key, request_hash, response_status, response_body, expires_at | 24 h, unique (business_id, user_id, key) |

### 6.2 Data

| Table | Key columns | Notes |
|---|---|---|
| `datasets` | id, business_id, version (int, per business), status (`uploaded`, `validating`, `ready`, `rejected`, `deleted`), row counts, date_min, date_max, checksum_sha256, uploaded_by | Unique (business_id, version). Immutable once `ready` (trigger) |
| `dataset_files` | id, business_id, dataset_id, kind, filename_sanitised, size_bytes, sha256, raw bytea (nullable after purge), purged_at | Raw is purged after 30 days |
| `validation_issues` | id, business_id, dataset_id, file_kind, row_number, column_name, code, severity, excerpt | — |
| `products`, `customers`, `sales_lines`, `returns` | business_id, dataset_id, natural key + §2 columns | PK (dataset_id, natural key). Insert-only (no UPDATE grant) |

### 6.3 Analysis and investigation

| Table | Key columns | Notes |
|---|---|---|
| `analysis_configs` | version (pk), constants jsonb | Global, read-only to the app |
| `data_overlays` | id, business_id, investigation_id, version, rules jsonb, created_by, reason | Unique (investigation_id, version). Insert-only |
| `analysis_revisions` | id, business_id, dataset_id, overlay_id (nullable), config_version, code_version, as_of_date, revision_hash, status | Unique on the revision key |
| `metric_results` | id, business_id, revision_id, metric, dimension, dimension_value, week, value, numerator, denominator, result_hash | Weekly grain |
| `customer_segments` | business_id, revision_id, customer_id, as_of, r, f, m, segment, flags[], recency_days, frequency, monetary | PK (revision_id, as_of, customer_id) |
| `investigations` | id, business_id, dataset_id, current_revision_id, status, phase, llm_mode, graph_version, last_seq, started_by, completed_at | — |
| `incidents` | id, business_id, investigation_id, revision_id, key, metric, dimension, value, rank, impact, stats jsonb, status (`investigated`, `not_investigated`) | Unique (revision_id, key) |
| `hypotheses` | id, business_id, incident_id, template, catalog_version, params jsonb, rejected_by_human, rejection_input_id | Unique (incident_id, template, params_hash) |
| `hypothesis_evaluations` | id, business_id, hypothesis_id, revision_id, status, confidence, data_confidence, human_confidence, criteria jsonb, evaluated_at | Insert-only history |
| `evidence_items` | id, business_id, revision_id, incident_id, tool, query_version, params jsonb, params_hash, result jsonb, result_hash, source (`data`, `human_context`), created_by_kind, human_input_id | Unique (revision_id, tool, params_hash). Insert-only |
| `clarifications` | id, business_id, investigation_id, incident_id, incident_key, criterion_key, trigger (CT-1..4), question_spec jsonb, rendered_text, status (`open`, `answered`, `applied`, `skipped`, `cancelled`), answered_by, answered_at | Unique (investigation_id, incident_key, criterion_key) |
| `human_inputs` | id, business_id, investigation_id, kind (`context`, `correction`, `rejection`, `findings_accept`, `findings_challenge`, `constraints`), ref_id, value jsonb, free_text, created_by, applied_at | Insert-only (except applied_at set once) |
| `findings` | id, business_id, investigation_id, round, claims jsonb, status (`pending_review`, `accepted`, `challenged`, `disputed`), llm_call_id | — |
| `llm_calls` | id, business_id, investigation_id, task, provider, model, prompt_version, input_hash, input jsonb, output jsonb, tokens_in, tokens_out, latency_ms, validation (`accepted`, `repaired`, `rejected_fallback`), error_code | — |
| `investigation_events` | id, business_id, investigation_id, seq, type, actor_kind (`system`, `llm`, `human`), actor_user_id, revision_id, payload jsonb, caused_by_event_id | Unique (investigation_id, seq). Insert-only |

### 6.4 Recovery and integrations

| Table | Key columns | Notes |
|---|---|---|
| `recovery_plans` | id, business_id, investigation_id, status (`awaiting_constraints`, `proposed`, `decided`), constraints jsonb, assumptions jsonb, constraints_by | — |
| `recovery_actions` | id, business_id, plan_id, strategy, action_type, payload jsonb, payload_hash, estimate jsonb (low, mid, high), status (§ARCH 10.5), integration_id | — |
| `approvals` | id, business_id, action_id, decision (`approved`, `rejected`, `revoked`), payload_hash, decided_by, reason | Insert-only |
| `action_executions` | id, business_id, action_id, attempt, status (`started`, `succeeded`, `failed`, `outcome_unknown`), idempotency_key, external_ref jsonb, error_code, started_at, finished_at | Unique (action_id, attempt) |
| `integrations` | id, business_id, kind (`google_sheets`, `slack_webhook`), status (`pending_verification`, `verified`, `revoked`), spreadsheet_id (unique where not revoked), verify_code_hash, verify_expires_at, secret_ciphertext, secret_key_id, created_by | Secret is never selected by read endpoints |
| `jobs` | id, business_id (nullable for system jobs), type, payload jsonb, status (`queued`, `running`, `succeeded`, `failed`, `cancelled`), priority, attempts, max_attempts, run_after, lease_expires_at, dedupe_key (unique), last_error_code | — |
| `audit_log` | id, business_id (nullable), actor_user_id, action, target_type, target_id, request_id, ip_prefix, metadata jsonb, at | Insert-only. Trigger denies UPDATE/DELETE |

### 6.5 Relationships

```
users ─< memberships >─ businesses ─< datasets ─< dataset_files, validation_issues, products, customers, sales_lines, returns
businesses ─< investigations ─< data_overlays ; investigations ─< analysis_revisions(dataset) ─< metric_results, customer_segments, evidence_items
investigations ─< incidents ─< hypotheses ─< hypothesis_evaluations ; incidents ─< evidence_items
investigations ─< clarifications, human_inputs, findings, llm_calls, investigation_events
investigations ─< recovery_plans ─< recovery_actions ─< approvals, action_executions ; recovery_actions >─ integrations
```

## 7. HTTP API (v1)

### 7.1 Conventions

- Base path `/api/v1`. JSON request and response bodies use `snake_case`.
- Unsafe methods require the header `X-CSRF-Token` (SECURITY_MODEL §4.3).
- Business-scoped routes are `/api/v1/businesses/{business_id}/…`. Child resources are addressed under the business (e.g. `/businesses/{bid}/actions/{aid}`), so tenant context is always explicit.
- **Pagination:** `?limit=` (1–100, default 50) and `?cursor=` (opaque). Response `{ "items": [...], "next_cursor": "..." | null }`.
- **Idempotency:** `Idempotency-Key` header (UUID) is **required** on `POST …/datasets`, `POST …/investigations`, `POST …/actions/{id}/approve` and `POST …/actions/{id}/retry`, and optional on other POSTs. A replay with the same key and body returns the stored response. The same key with a different body → 409 `idempotency_key_reuse`.
- **Async operations** return `202` with the resource in its queued state.
- **Error format** (RFC 9457, `Content-Type: application/problem+json`):

```json
{
  "type": "https://root.app/problems/validation-error",
  "title": "Request validation failed",
  "status": 422,
  "detail": "One or more fields are invalid.",
  "instance": "/api/v1/businesses/…/datasets",
  "code": "validation_error",
  "request_id": "01J…",
  "errors": [{ "field": "dimension", "code": "invalid_dimension", "message": "Unsupported dimension." }]
}
```

Standard codes:

| Status | Codes |
|---|---|
| 400 | `bad_request` |
| 401 | `unauthenticated` |
| 403 | `forbidden`, `csrf_failed` |
| 404 | `not_found` (also for cross-tenant access) |
| 409 | `conflict`, `stale_payload`, `invalid_state`, `idempotency_key_reuse`, `integration_already_bound` |
| 413 | `payload_too_large` |
| 415 | `unsupported_media_type` |
| 422 | `validation_error`, `invalid_dimension`, `constraint_violation` |
| 429 | `rate_limited` (with `Retry-After`) |
| 500 | `internal_error` (no internals) |
| 502 | `upstream_error` |
| 503 | `unavailable` |

### 7.2 Auth

| Method | Path | Auth | Body → Response |
|---|---|---|---|
| GET | `/auth/csrf` | none | → `{csrf_token}`. Sets the pre-session CSRF cookie |
| POST | `/auth/register` | none | `{email, password, display_name}` → 201 `{user}`. Sets the session |
| POST | `/auth/login` | none | `{email, password}` → 200 `{user}`. Rotates the session |
| POST | `/auth/logout` | session | → 204 |
| GET | `/auth/me` | session | → `{user, memberships:[{business_id, name, role}]}` |

### 7.3 Businesses and members

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/businesses` | authenticated | `{name, currency, timezone}` → 201. The creator becomes owner |
| GET | `/businesses/{bid}` | `business:read` | — |
| PATCH | `/businesses/{bid}` | `business:update` | name, timezone (currency is immutable after the first dataset) |
| DELETE | `/businesses/{bid}` | `business:delete` | Requires `{confirm_name}`. 202 (purge job) |
| GET | `/businesses/{bid}/members` | `member:read` | — |
| POST | `/businesses/{bid}/members` | `member:manage` | `{email, role}`. The user must already exist (no email sending, NG scope). Unknown email → 404-equivalent generic message |
| PATCH/DELETE | `/businesses/{bid}/members/{uid}` | `member:manage` | Cannot remove or demote the last owner (409) |

### 7.4 Datasets

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/businesses/{bid}/datasets` | `dataset:upload` | multipart: `products`, `customers`, `sales`, `returns` → 202 `{dataset}` |
| GET | `/businesses/{bid}/datasets` | `dataset:read` | paginated |
| GET | `/businesses/{bid}/datasets/{did}` | `dataset:read` | status, counts, date range, issue counts by code |
| GET | `/businesses/{bid}/datasets/{did}/issues` | `dataset:read` | `?code=&file=` paginated |
| DELETE | `/businesses/{bid}/datasets/{did}` | `dataset:delete` | 409 if referenced by a non-terminal investigation |

### 7.5 Analytics (on the dataset's base revision, or on an explicit `revision_id`)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/businesses/{bid}/datasets/{did}/metrics` | `analytics:read` | `?metrics=net_revenue,refund_rate&dimension=region&from=&to=&revision_id=` → `{series:[{metric, dimension_value, points:[{week, value, numerator, denominator}]}], revision_id, revision_hash}` |
| GET | `/businesses/{bid}/datasets/{did}/segments` | `analytics:read` | → `{as_of, low_sample, distribution:[{segment, customers, monetary}], flags:{churn_risk, return_risk, key_account}}` |

### 7.6 Investigations

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/businesses/{bid}/investigations` | `investigation:run` | `{dataset_id}` → 202. 409 if the dataset is not `ready` |
| GET | `/businesses/{bid}/investigations` | `investigation:read` | paginated |
| GET | `/businesses/{bid}/investigations/{iid}` | `investigation:read` | projection at the latest seq (ARCH §9.2) |
| POST | `/businesses/{bid}/investigations/{iid}/cancel` | `investigation:cancel` | — |
| POST | `/businesses/{bid}/investigations/{iid}/retry` | `investigation:run` | only when `failed` |
| GET | `/businesses/{bid}/investigations/{iid}/events` | `investigation:read` | `?after_seq=&limit=` → `{items, last_seq}` |
| GET | `/businesses/{bid}/investigations/{iid}/replay` | `investigation:read` | `?at_seq=` → projection |
| GET | `/businesses/{bid}/investigations/{iid}/human-impact/{event_seq}` | `investigation:read` | before/after view (ARCH §9.3) |
| GET | `/businesses/{bid}/investigations/{iid}/incidents` | `investigation:read` | — |
| GET | `/businesses/{bid}/investigations/{iid}/hypotheses` | `investigation:read` | with latest evaluation and criteria |
| GET | `/businesses/{bid}/evidence/{eid}` | `investigation:read` | tool, params, result, hash, revision |
| GET | `/businesses/{bid}/investigations/{iid}/revisions/compare` | `investigation:read` | `?from=&to=` |
| POST | `/businesses/{bid}/investigations/{iid}/revisions/{rid}/reverify` | `investigation:run` | 202 |

### 7.7 Human-in-the-loop

| Method | Path | Permission | Body |
|---|---|---|---|
| GET | `/businesses/{bid}/investigations/{iid}/clarifications` | `investigation:read` | — |
| POST | `/businesses/{bid}/clarifications/{cid}/answer` | `hitl:answer` | `{answer: {type, option_ids?, boolean?, date_range?, free_text?, record_ids?}}` validated against `question_spec` → 202 |
| POST | `/businesses/{bid}/clarifications/{cid}/skip` | `hitl:answer` | `{reason?}` → 202 |
| POST | `/businesses/{bid}/investigations/{iid}/corrections` | `hitl:answer` | `{kind, order_line_ids? \| customer_ids? \| return_ids?+reason_code \| date_range?, reason}` → 202 |
| POST | `/businesses/{bid}/hypotheses/{hid}/reject` | `hitl:answer` | `{reason}` → 202 |
| POST | `/businesses/{bid}/findings/{fid}/review` | `hitl:answer` | `{decision: accept \| challenge, reason?, alternative_hypothesis_id?, context?}` → 202 |
| POST | `/businesses/{bid}/recovery-plans/{pid}/constraints` | `hitl:answer` | `{constraints, assumptions}` → 202 |

Every answer endpoint returns 409 `invalid_state` unless the target is awaiting exactly that input.

### 7.8 Recovery actions

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/businesses/{bid}/recovery-plans/{pid}` | `investigation:read` | plan, constraints, assumptions, actions, estimates |
| PATCH | `/businesses/{bid}/actions/{aid}` | `action:edit` | only `proposed`. Re-validates. Returns the new `payload_hash` |
| POST | `/businesses/{bid}/actions/{aid}/approve` | `action:approve` | `{payload_hash}` → 202. 409 `stale_payload` |
| POST | `/businesses/{bid}/actions/{aid}/reject` | `action:reject` | `{reason}` → 200 |
| POST | `/businesses/{bid}/actions/{aid}/revoke` | `action:approve` | — |
| POST | `/businesses/{bid}/actions/{aid}/retry` | `action:retry` | only `failed` → 202 |
| POST | `/businesses/{bid}/actions/{aid}/resolve` | `action:retry` | only `outcome_unknown`: `{resolution: reconcile \| retry \| mark_failed}` |
| GET | `/businesses/{bid}/actions/{aid}/executions` | `investigation:read` | — |

### 7.9 Integrations

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/businesses/{bid}/integrations` | `integration:read` | Never returns secrets or verification codes |
| POST | `/businesses/{bid}/integrations/google-sheets` | `integration:manage` | `{spreadsheet}` (URL or ID) → 201 `{id, service_account_email, verification_code, expires_at}`. The code is shown once |
| POST | `/businesses/{bid}/integrations/{id}/verify` | `integration:manage` | → 200 `verified`, or 422 `verification_failed` |
| POST | `/businesses/{bid}/integrations/slack` | `integration:manage` | `{webhook_url}` → 201 |
| POST | `/businesses/{bid}/integrations/{id}/test` | `integration:manage` | rate-limited |
| DELETE | `/businesses/{bid}/integrations/{id}` | `integration:manage` | revoke |

### 7.10 Audit and operations

| Method | Path | Permission |
|---|---|---|
| GET | `/businesses/{bid}/audit-log` | `audit:read` (`?action=&from=&to=` paginated) |
| GET | `/healthz` | none (liveness, no DB) |
| GET | `/readyz` | none (DB reachable, migrations at head) |

The OpenAPI schema is generated by FastAPI and used to generate frontend types (`openapi-typescript`). Contract drift fails CI (TEST_STRATEGY §7).

## 8. Investigation events

Envelope: `{seq, type, occurred_at, actor_kind, actor_user_id?, revision_id?, caused_by_seq?, payload}`.

| Type | Payload (IDs and small values only) |
|---|---|
| `investigation.created` | dataset_id, llm_mode |
| `run.started` / `run.resumed` | job_id |
| `revision.created` | revision_id, overlay_version, revision_hash |
| `metrics.computed` | revision_id, metric_count |
| `incidents.detected` | incident ids with rank, metric, slice, impact |
| `incident.selected` | incident_id |
| `hypotheses.generated` | incident_id, hypothesis ids + templates, source (`catalog`, `llm_prioritized`) |
| `evidence.collected` | evidence ids, tools |
| `hypothesis.evaluated` | hypothesis_id, status, confidence, data_conf, human_conf, criteria satisfied |
| `clarification.requested` | clarification_id, trigger, incident_id |
| `clarification.answered` / `clarification.skipped` | clarification_id, human_input_id |
| `human.context_added` | human_input_id, evidence_id |
| `human.correction_applied` | human_input_id, overlay_version, new revision_id |
| `hypothesis.rejected_by_human` | hypothesis_id, human_input_id |
| `clarification.cap_reached` | incident_id |
| `findings.drafted` | findings_id, round, root_cause hypothesis ids |
| `findings.accepted` / `findings.challenged` / `findings.disputed` | findings_id, human_input_id |
| `plan.awaiting_constraints` / `plan.constraints_set` | plan_id |
| `plan.proposed` | plan_id, action ids |
| `action.edited` / `action.approved` / `action.rejected` / `action.revoked` | action_id, payload_hash |
| `action.execution_started` / `…_succeeded` / `…_failed` / `…_outcome_unknown` | action_id, attempt, external_ref?, error_code? |
| `llm.call_completed` | llm_call_id, task, validation |
| `llm.fallback_used` | task, reason (`budget`, `provider_error`, `validation`) |
| `reverify.completed` | revision_id, matches, mismatches |
| `investigation.awaiting_human` | kind, ref_id |
| `investigation.completed` / `…_no_incidents` / `insufficient_data` / `failed` / `cancelled` | summary or error_code |

## 9. LLM contracts

### 9.1 Common input envelope

```json
{
  "task": "synthesize_findings",
  "prompt_version": "synthesize_findings.v1",
  "context": {
    "business": {"currency": "USD"},
    "incident": {"id": "inc_…", "metric": "refund_rate", "slice": {"dimension": "category", "entity_ref": "{{entity:category:…}}"}},
    "evidence": [{"id": "ev_…", "tool": "reason_mix_shift", "fields": {"pp_change_damaged_defective": "0.142"}}],
    "hypotheses": [{"id": "hyp_…", "template": "H_RETURNS_QUALITY", "status": "supported", "confidence": "0.82"}],
    "human_inputs": [{"id": "hi_…", "kind": "context", "structured": {"option_id": "supplier_change"}}]
  },
  "untrusted_text": [{"source": "human_free_text", "ref": "hi_…", "text": "…"}],
  "allowed": {"evidence_ids": ["…"], "hypothesis_ids": ["…"], "templates": ["…"], "placeholders": ["ev:…", "entity:…"]}
}
```

The LLM may read numbers in its input. It may not **write** digits or entity names outside placeholders.

### 9.2 Outputs (JSON Schema, strict, `additionalProperties: false`)

**`prioritize_hypotheses`**
```json
{"ranking": [{"hypothesis_id": "string", "priority": "integer 1-9", "rationale": [{"text": "string<=300", "evidence_refs": ["string"]}]}]}
```

**`request_evidence`.** Uses provider tool calling. Tools are generated from the evidence registry parameter models, so arguments are validated by the same Pydantic models. A maximum of 6 calls applies.

**`phrase_clarification`**
```json
{"question_text": "string<=300", "why_asking": "string<=400", "evidence_refs": ["string"]}
```

**`synthesize_findings`**
```json
{"summary": "string<=600",
 "root_causes": [{"hypothesis_id": "string", "statement": "string<=400", "evidence_refs": ["string"]}],
 "alternatives_considered": [{"hypothesis_id": "string", "why_not": "string<=300", "evidence_refs": ["string"]}],
 "caveats": ["string<=300"],
 "human_influence": [{"human_input_id": "string", "effect": "string<=300"}]}
```

**`propose_strategies`**
```json
{"strategies": [{"strategy": "S_…", "hypothesis_id": "string",
  "params": {"target_segments": ["string"], "discount_pct": "decimal-string", "description_key": "string"},
  "rationale": [{"text": "string<=300", "evidence_refs": ["string"]}]}]}
```

`discount_pct` is the only number the LLM may emit. It is a *proposal parameter*, never a financial fact. The validator bounds it by `max_discount_pct`, and a human must approve it.

### 9.3 Validation and rendering

The pipeline is ARCHITECTURE §8.3.

**Placeholder grammar:**

- `{{ev:<evidence_id>.<field>}}` where the field is a key in that evidence item's result fields.
- `{{entity:<category|product|region|channel|segment>:<server_ref>}}`.

Rendering formats each value using the business currency and locale. Rates are formatted as percentages with 1 dp.
