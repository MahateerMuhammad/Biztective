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

> **Amended by AMD-8 (§10):** the order-line file is `orders.csv`. Header matching follows the mapping procedure in §10.2, and validation codes are extended in §10.4.

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

> **Amended by AMD-8:** see §10.4 for the as-built rule set (new V-017 and V-044, cumulative-return semantics, issue storage cap).

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

> **Amended by AMD-10 and AMD-11:** cancelled lines are excluded from L, COGS-based metrics need full cost coverage, and default API ranges are defined. As built: §11.

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

> **Amended by AMD-10:** the segment table below is replaced by lifecycle segments (`loyal`, `at_risk`, `dormant`, `insufficient_history`), and the `churn_risk` flag is removed. RFM scores, `return_risk` and `key_account` are unchanged. As built: §11.3.

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

> **Amended by AMD-3:** RLS is ENABLE, with a non-owner/non-superuser/non-BYPASSRLS runtime role enforced by readiness; FORCE below is the baseline. **Amended by AMD-4:** the identity/session column changes in ROOT_MASTER_PLAN §13 replace §6.1 where specified.

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

> **Amended by AMD-8:** as built, see §10.6 (`order_lines` instead of `sales_lines`, new dataset statuses and columns, `jobs`, `idempotency_keys`).

| Table | Key columns | Notes |
|---|---|---|
| `datasets` | id, business_id, version (int, per business), status (`uploaded`, `validating`, `ready`, `rejected`, `deleted`), row counts, date_min, date_max, checksum_sha256, uploaded_by | Unique (business_id, version). Immutable once `ready` (trigger) |
| `dataset_files` | id, business_id, dataset_id, kind, filename_sanitised, size_bytes, sha256, raw bytea (nullable after purge), purged_at | Raw is purged after 30 days |
| `validation_issues` | id, business_id, dataset_id, file_kind, row_number, column_name, code, severity, excerpt | — |
| `products`, `customers`, `sales_lines`, `returns` | business_id, dataset_id, natural key + §2 columns | PK (dataset_id, natural key). Insert-only (no UPDATE grant) |

### 6.3 Analysis and investigation

> **Amended by AMD-12 (Phase 5):** investigation tables as built are in §12.5.

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

> **Amended by AMD-8:** as built, see §10.7 (mapping confirmation, records and export endpoints; `DELETE` deferred to Phase 12).

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

> **Amended by AMD-12 (Phase 5):** endpoints as built are in §12.6.

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

> **Amended by AMD-12 (Phase 5):** `request_evidence` is a structured output (`select_tools`, §12.4), not provider tool calling. `synthesize_findings` has no `human_influence` field in Phase 5.

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

## 10. Phase 3 ingestion contract (AMD-8, as built)

### 10.1 Upload request

`POST /api/v1/businesses/{business_id}/datasets` (`dataset:upload`, owner or analyst). Requirements:

- A CSRF token and an **`Idempotency-Key`** header (8–100 characters, `[A-Za-z0-9_-]`) are required.
- The body is `multipart/form-data` with exactly the parts `orders`, `products`, `customers` and `returns` (one file each), plus the optional fields:
  - `source_timezone`: IANA name, default is the business time zone.
  - `currency`: must equal the business currency.
- Authorization is checked **before** the body is parsed. Multipart parsing allows at most 4 files and 2 fields. File parts are spooled to temporary files that are deleted when the request ends.

**Request-time checks.** Each one rejects the upload synchronously, and no dataset is created:

| Check | Status / code |
|---|---|
| Not multipart | 415 `unsupported_media_type` |
| Missing, duplicate or unexpected parts | 422 `missing_files`, `duplicate_field` or `unexpected_field` |
| Extension not `.csv`, or content type not CSV-like (`text/csv`, `application/csv`, `text/plain`, `application/vnd.ms-excel`, `application/octet-stream`) | 415 `invalid_file_type` |
| File > 25 MB, or request > 60 MB | 413 `file_too_large` or `payload_too_large` |
| Binary signature (ZIP/XLSX, OLE, PDF, gzip, UTF-16 BOM) or NUL bytes | 422 `corrupt_file` |
| Not strict UTF-8 (byte offset reported) | 422 `invalid_encoding` |
| Empty file, or header-only (except `returns`) | 422 `empty_file` |
| Header not parseable, or more than 100 columns | 422 `invalid_csv` |
| Empty or duplicate header names after normalisation | 422 `invalid_header` or `duplicate_header` |
| A required column has no exact or synonym match | 422 `missing_required_columns`, with the accepted header names listed per column |
| `source_timezone` unknown | 422 `invalid_timezone` |
| `currency` malformed, or not equal to the business currency | 422 `invalid_currency` or `currency_mismatch` |
| Identical files to a non-failed version of this business | 409 `duplicate_dataset` (names the version) |
| More than 10 uploads per business per hour | 429 `rate_limited` |
| Same `Idempotency-Key` with different content | 409 `idempotency_key_reuse` |

Same key with same content returns the stored 202 response; keys last 24 h. Success is **202** with the dataset detail. Status is `awaiting_mapping` if any column needs a decision, otherwise `queued`.

### 10.2 Column mapping and the human checkpoint

- **Normalisation:** trim, lowercase, strip the BOM, turn spaces, hyphens, dots and slashes into `_`, and drop other symbols.
- **Mapping status per canonical column:**
  - `exact` (the header equals the canonical name; auto-accepted)
  - `proposed` (exactly one synonym found)
  - `ambiguous` (several synonyms, or one header proposed for two columns)
  - `unmapped`
- Synonyms are listed in `app/ingestion/contract.py`.
- **Any `proposed` or `ambiguous` column puts the dataset in `awaiting_mapping`.** No job is queued until `POST …/datasets/{id}/mapping` with `{"files": {"<kind>": {"<canonical>": "<header>" | null}}}` decides **every** pending column.
  - The chosen header must exist in the file, must not be personal data, and must not be used twice.
  - Required columns cannot be null.
  - Violations return 422 `invalid_mapping` with per-column errors. A second confirmation returns 409 `invalid_state`.
- **Personal data:** in `customers`, `orders` and `returns`, headers whose tokens indicate personal data (email, phone, address, postcode, birth date, card, IP, or `*_name` other than product, category, channel, region, store, brand, campaign or item names) are **removed from the stored file before it reaches the database** and reported as V-041. If a row's field count does not match the header, it cannot be split safely, so it is replaced by an empty line and reported as V-010 at the same row number. `products` is exempt, because its `name` is a product name.
- Headers that are neither mapped nor personal data are ignored and reported as V-040.

### 10.3 Value interpretation

- **Dates and times:** ISO 8601 only (`YYYY-MM-DD`, `YYYY-MM-DD[T ]HH:MM[:SS[.ffffff]][Z|±HH:MM]`).
  - Slashed or dotted dates are rejected as ambiguous, never guessed.
  - Date-only values are business-local calendar dates.
  - Naive date-times are read in `source_timezone`; values with an offset are converted.
  - Storage is the calendar date in the **business time zone captured on the dataset at upload**, plus `ordered_at`/`returned_at` (`timestamptz`, UTC) when a time was given.
  - Nonexistent local times (spring-forward gap) are rejected (V-013). Ambiguous fall-back times use the first occurrence and are flagged V-044.
  - The valid range is 2000-01-01 to (upload date in the business zone + 1 day).
- **Money:** plain decimals with up to 4 places, stored as `NUMERIC(18,4)`. Negative values, separators, symbols, exponents, NaN and infinity are rejected.
- **Quantities:** whole numbers from 1 to 100,000.
- **Dimensions:** whitespace is collapsed and values are lowercased. Missing `channel` or `region` on order lines are stored as `unknown`.
- **Currency column (orders, optional):** each value must be a 3-letter code equal to the business currency (V-017). The business currency is what gets stored.
- **Text:** control and format characters are removed. `reason_text` is limited to 500 characters and is stored as untrusted data.

### 10.4 Validation rules as built

Codes, severities and descriptions live in `ISSUE_CODES` in `app/ingestion/contract.py` and are shown in the report.

**Additions and changes to §2.6:**

- **V-002:** structural CSV errors inside the file (for example an unterminated quote) reject that file.
- **V-003:** row limits reject the file.
- **V-017** (new): currency.
- **V-044** (new): ambiguous local time, warning.
- **V-035 / V-036, cumulative semantics:** the running total per order line, in `(return_date, return_id)` order, includes every earlier return that passed the other rules. A return is rejected when that total exceeds the line quantity or the line net value (`quantity × unit_price − discount_amount`).
- **Evaluation order is deterministic:**
  1. per-row parsing
  2. duplicates (V-020 / V-021)
  3. order consistency (V-037)
  4. references (V-030, V-031, then V-032)
  5. V-033
  6. V-034
  7. V-035
  8. V-036
  9. V-043 warnings

  Each row receives at most one set-based error.
- **Rejected rows cascade:** an order line whose customer or product was rejected fails V-030 or V-031, and its returns fail V-032.
- **Dataset rejection:** if rejected rows exceed 5% of any file (V-099), or any file-level error occurs (V-002, V-003, V-006), or no valid order lines remain (V-007), the dataset becomes `rejected`. Issues and profiles are stored, but **no records are**.
- **Issue storage:** issue counts are exact. At most 10,000 issue rows are stored per (file, code), and the full stored list can be exported as CSV.

### 10.5 Processing and atomicity

The `ingest_dataset` job (ADR-003 queue: `FOR UPDATE SKIP LOCKED`, 120 s lease with 30 s heartbeat, at most 3 attempts with exponential backoff, transient database errors retried) proceeds as follows:

1. Mark the dataset `validating` (its own short transaction).
2. In **one transaction**:
   - Stream-decode each stored file and parse row by row.
   - `COPY` typed rows into transaction-scoped temp staging tables.
   - Run the set-based rules.
   - Insert accepted rows into the canonical tables with `INSERT … SELECT`.
   - Write file statistics, profiles and the final status.

   PostgreSQL forbids `COPY FROM` into tables with row-level security, so issues are copied into a temp table and moved with one `INSERT … SELECT`, which is subject to the RLS `WITH CHECK`.
3. Any exception rolls the whole transaction back. After the final attempt the dataset is set to `failed` with a generic `error_code` (`unexpected_error`, `transient_error`, …). No partial records exist in any case.
4. A `failed` version does not block re-uploading the same files.

**Provenance:** every record keeps `source_row` (header = row 1). `dataset_files` keeps the original SHA-256, size, sanitised filename, normalised headers, the mapping used and who confirmed it. Raw bytes are stored without personal-data columns, and are purged after 30 days (retention job, Phase 12).

### 10.6 Schema (migration `0002`)

| Table / change | Notes |
|---|---|
| `datasets` | Status ∈ `awaiting_mapping`, `queued`, `validating`, `ready`, `rejected`, `failed`. New columns: `source_timezone`, `business_timezone`, `currency`, `issue_counts` (exact counts by file and code), `error_code`, `completed_at`. `sales_rows` is renamed `orders_rows`. A trigger makes `ready`, `rejected` and `failed` rows immutable |
| `dataset_files` | One per kind and dataset. Columns: `raw` (bytea), `headers`, `mapping`, confirmation, row statistics, `profile` |
| `products`, `customers`, `order_lines`, `returns` | Primary key `(dataset_id, natural key)`. Composite foreign keys enforce references within a dataset. CHECK constraints duplicate the value rules. Insert-only for the app role |
| `validation_issues` | Identity primary key (stable order). Insert-only |
| `idempotency_keys` | Primary key `(business_id, user_id, key)`, 24 h |
| `jobs` | System table: no RLS, never exposed through the API. The app role cannot change job identity, type or payload |

All tenant tables have RLS select and insert policies keyed on `app.current_business_id`. The job worker sets only the business context.

### 10.7 Endpoints

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/businesses/{bid}/datasets` | `dataset:upload` | §10.1 |
| GET | `/businesses/{bid}/datasets/{did}` | `dataset:read` | Dataset, files (mapping, statistics, profile) and the `issues` summary (file, code, severity, description, count) |
| POST | `/businesses/{bid}/datasets/{did}/mapping` | `dataset:upload` | §10.2. Returns 202 |
| GET | `/businesses/{bid}/datasets/{did}/issues` | `dataset:read` | `?file=&code=&limit=&cursor=` |
| GET | `/businesses/{bid}/datasets/{did}/records/{orders\|products\|customers\|returns}` | `dataset:read` | Ready datasets only (else 409 `dataset_not_ready`). Ordered by `source_row`. Decimals are returned as strings |
| GET | `/businesses/{bid}/datasets/{did}/export/{orders\|products\|customers\|returns\|issues}` | `dataset:read` | CSV attachment. Every cell starting with `= + - @`, tab or CR is prefixed with `'` (SEC-16). Built in a bounded spooled temp file |
| PATCH | `/businesses/{bid}` | `business:update` (owner) | `{currency?, timezone?}`. A currency change returns 409 `currency_locked` once any dataset exists |

## 11. Phase 4 analytics contract (AMD-10, AMD-11, as built)

### 11.1 Scope of L and R

- **L:** order lines with `status = 'completed'` in the period. Cancelled lines are excluded from every metric and reported as `cancelled_excluded`.
- **R:** returns by `return_date` in the period. Returns of cancelled lines cannot exist (V-038).
- **Slices:** a return takes its line's slice.
- **Repeat and new customers** (M18, M19) use each customer's first completed order over the whole dataset.
- **Weekly views:** the "period" is the ISO week (Monday to Sunday), and multi-week ratios are Σ numerator / Σ denominator (A11).

### 11.2 Formulas exactly as implemented (`app/analytics/metrics.py`)

M01–M19 are as §3.3. In addition:

- `cogs_sold` is defined only if every line in scope has a `unit_cost`. `cogs_restocked` is defined only if every restockable return (reason not damaged or defective) in scope has a cost. `gross_profit` and `gross_margin_pct` need both. A cost is never estimated from other products.
- **M20** (`net_revenue_per_active_customer_week`) is Σ weekly net revenue / Σ weekly active customers. It is used for detection impacts.
- **M21** (`matured_cohort_refund_rate`) covers lines ordered in [start, min(end, T − 14 d)], counting their refunds dated within 14 days of the order. It is shown per product.
- **Precision:**
  - Arithmetic runs in a 60-digit decimal context, with ROUND_HALF_EVEN.
  - Money is output as strings with 4 dp (aov included).
  - Ratios are output as strings with 6 dp.
  - Counts are integers.
  - Undefined values are `null`.

### 11.3 Customer segmentation

- **As-of date T:** the last completed order date. Only completed orders up to T count, and only refunds dated on or before T.
- **Inputs:** recency (T − last order), lifetime distinct orders, distinct orders in the trailing 365 days (F), and net revenue in the trailing 365 days (M). Refunds are attributed through the line.
- **Cadence:** `median_gap_days` is the median gap between consecutive distinct order dates.
- **Rules:** the lifecycle rules are as AMD-10, with constants in `analysis_config` v1 (`dormant_days` 180, `dormant_gap_multiplier` 4.0, `churn_gap_multiplier` 2.0, `churn_min_recency_days` 30, `min_history_days` 60). Customers with only cancelled lines are not segmented.
- **Scores and flags:** RFM scores and the `return_risk` and `key_account` flags are as §4.
- **Storage:** results are stored per analysis revision in `customer_segments`.

### 11.4 Detection, severity, records in scope and evidence

**Detection** follows ARCHITECTURE §5, with the AMD-10 and AMD-11 details:
- 4 current complete weeks against up to 12 baseline complete weeks; at least 8 baseline weeks with orders are required.
- Slices are overall, category, region, channel, product (top 80% of baseline net sales, at most 50) and customer segment (segments as of the end of the baseline).
- Minimum support, robust z (1.4826 × MAD), and the change, materiality and impact formulas are as ARCHITECTURE §5.
- Consolidation: parent suppression, then a Jaccard overlap merge of W order sets ≥ 0.60 across dimensions, then ranking. At most 5 incidents are kept, and the top 3 are marked `investigated`.

**Severity** is the impact as a share of overall net revenue in W: ≥ 5% `critical`, ≥ 2.5% `high`, ≥ 1% `medium`, otherwise `low`.

**Records in scope** (`affected`) are the slice's completed order lines, distinct orders and customers in W, plus its returns in W. This is scope, not causation.

**Evidence items** are insert-only, and each `result_hash` is the SHA-256 of canonical JSON:

| Kind | Content |
|---|---|
| `incident_detection` | Weekly B and W series, median, MAD, σ̂, current value, z, Δ, impact, materiality, onset week, support counts, config version |
| `source_records` | Reproducible filter (table, dataset, window, slice) and exact counts. Up to 25 source references `{file, source_row, key, amount, detail}`, ordered by contribution: refund amount for refund metrics, discount for discount metrics, line net sales otherwise |
| `return_reason_mix` | For refund incidents: refunds and shares by reason code, B against W |

**Reproducibility:** the revision hash is SHA-256 over the hashes of the weekly metric snapshot, the segment list and the incidents (with their evidence hashes). Recomputing on the same revision yields identical hashes, which is tested.

### 11.5 Endpoints

All require `analytics:read` (owner, analyst, viewer) unless noted. Each takes `/businesses/{bid}/datasets/{did}` as prefix. The dataset must be `ready`, otherwise 409 `dataset_not_ready`. A dataset with no completed lines returns 409 `no_completed_orders`.

| Method | Path | Notes |
|---|---|---|
| GET | `/metrics` | `?from&to&granularity=total\|week&dimension=overall\|category\|product\|region\|channel\|customer_segment\|acquisition_channel`. Default range is [first order, last activity]. At most 1,100 days. `customer_segment` needs a finished analysis (else 409 `analysis_not_ready`) |
| GET | `/metrics/comparison` | Current period against the immediately preceding equal-length period. The default current period is the last 4 complete weeks. `previous: null` plus `comparison_unavailable` when the earlier period starts before the data |
| GET | `/products` | Per product: all metrics plus `matured_cohort_refund_rate`, ordered by net revenue. `?limit` up to 500 |
| GET | `/segments` | Distribution, monetary totals and human-readable rules from the latest finished analysis |
| GET | `/customers` | `?segment&limit&cursor`. Pseudonymous customer IDs with cadence statistics and flags |
| GET | `/analysis` | Latest revision: status, windows, restrictions, summary (including detection coverage) and incidents with evidence references |
| POST | `/analysis` | `analysis:run` (owner, analyst). Idempotent per (dataset, config, code). A `failed` revision is re-queued |
| GET | `/incidents/{iid}` | Incident with full evidence payloads |

Every analytics response carries `restrictions[]` with codes `missing_costs`, `incomplete_period`, `comparison_unavailable`, `insufficient_history`, `immature_returns`, `low_sample` and `cancelled_excluded`.

### 11.6 Schema (migration `0003`)

- `order_lines.status`: `completed` or `cancelled`, with a partial index on completed lines.
- `products.unit_cost` becomes nullable.
- `analysis_configs`: v1 seeded, read-only to the app.
- `analysis_revisions`: unique per (dataset, config version, code version). `completed` and `insufficient_data` rows are immutable (trigger).
- `customer_segments`, `incidents` and `evidence_items`: insert-only, with RLS.
- `refresh_dataset_statistics()` (AMD-11).
- The `jobs.type` values now include `analyze_dataset`.

## 12. Phase 5 investigation contracts (AMD-12)

> **Amended by AMD-12 and AMD-13:** these are the implemented Phase 5 shapes. Human answers, corrections, findings acceptance and execution remain outside this phase.

### 12.1 Scope and evidence facts

The runtime supplies business, dataset, revision, incident, metric/slice, baseline/current windows, first-order date, as-of date and config. Tool arguments cannot override that scope. Queries execute in the tenant transaction. Results are `{facts: {field: {value, unit, kind?}}, detail: object}`. Units: `money`, `ratio`, `pp`, `count`, `number`, `entity`, `code`. Numbers are decimal strings except integer counts; undefined values are null. Entity kinds: category/product/region/channel/segment. A fractional rate change of `0.02` renders as `+2.0 pp`.

Evidence retains IDs, revision/incident, kind, canonical parameters, query_version and SHA-256 result_hash. Tool evidence also has params_hash; unique `(revision_id, kind, params_hash)` caches immutable calculations. Parameters include incident scope. `investigation_evidence` links cached items with requested_by=detection/system/llm. Lookup/citations require the current investigation and incident link; hashes are checked before verification. Provider inputs contain aggregates, codes and opaque entity tokens only. Raw entity values, customer IDs, business descriptions, CSV free text and source rows never reach the provider.

### 12.2 Strict read-only tools (query version 1)

All argument objects forbid extra fields and coercion. No tenant IDs, entity values, SQL, Python, shell or arbitrary filters are accepted. Metric enum M: net_revenue/gross_margin_pct/refund_rate/discount_rate/active_customers/repeat_customer_rate/aov.

| Tool | Required arguments | Output facts and semantics |
|---|---|---|
| metric_breakdown | `{metric:M, by:none/category/product/region/channel/customer_segment}` | total.baseline/current/change/adverse; values, additive, top_share, top.N.entity/baseline/current/adverse/share. Amounts and active counts are weekly averages; rates/AOV are ratios of sums. Positive adverse contributions define shares. Overlapping customer counts across product/category have no additive shares. |
| period_comparison | `{metrics:M[]}`; 1–7 unique | metric.current/previous/change_abs/change_rel. Current versus preceding equal-length period; comparison_unavailable when prior starts before data. Zero denominators remain null. |
| return_investigation | `{by:product/category/region/channel}` | Refund totals, reason shares/changes, quality and logistics shares, contributors.*, cohort.baseline_rate/current_rate/change and line counts. Cohorts require configured maturity; immature current data is restricted. |
| segment_analysis | `{}` | segments.segment.active_baseline/active_current/active_change_rel/revenue_decline; established.* combines loyal/at_risk. Membership fixed at baseline end; later first buyers are new. |
| customer_exposure | `{top_k:integer}`; 1–20 | customers.current/adverse, adverse.total, top_k, top_k.share, top1.share, exposed.segment, repeat_buyers.current. Revenue-decline basis except refund/discount incidents use excess refunds/discounts at slice baseline rate. Pseudonymous top-customer details are human-only. |
| evidence_lookup | `{evidence_ids:UUID[]}`; 1–10 | Facts already linked to this investigation and incident. Foreign or unlinked IDs rejected. |
| data_quality_check | `{checks:enum[]}`; 1–6 unique | zero_order_days, weekly_volume, near_duplicates, cancellations, missing_costs, immature_returns. Scoped counts, shares and weekly anomaly statistics; undefined ratios null. |

Breakdowns retain ten detail rows plus remainder, with five leading rows exposed as facts. Six LLM-requested calls and forty evidence items per incident maximum; duplicate calls collapse. Catalog-required fixed calls run independently of model selection, so verdicts never depend on LLM choices.

### 12.3 Hypothesis catalog 1.0.0

All applicable templates run, always including data quality and external context. Table order breaks confidence ties. Every criterion records evidence_id, field, value and unit in basis; missing/null data is unknown.

| Template | Fixed requirements | Data support (weight) | Explicit refutation |
|---|---|---|---|
| H_RETURNS_QUALITY | returns by product | Quality-share rise ≥ .10 (.4); top-product excess-refund share ≥ .50 (.3); mature cohort rise ≥ .02 (.3) | Quality-share rise < .02 and product criterion false |
| H_FULFILLMENT | returns by region/channel | Logistics-share rise ≥ .10 (.5); leading region/channel excess-refund share ≥ .50 (.5) | Logistics rise < .02 and concentration false |
| H_PRICE_DISCOUNT | discount_rate by product | Rate rise ≥ .02 (.6); top-product share ≥ .50 (.4) | Rate rise < .005 |
| H_SEGMENT_SHIFT | segment analysis | Established active relative change ≤ −.15 (.5); established revenue-decline share ≥ .50 (.5) | Active change ≥ −.05 and decline share < .50 |
| H_CONCENTRATION | exposure top_k=5 | Top-five share ≥ .50 (.6); top-one share ≥ .25 (.4) | Top-five share < .20 |
| H_CHANNEL | incident metric by channel | ≥2 channels and leading adverse share ≥ .60 (1) | ≥2 channels and leading share < .40 |
| H_DATA_QUALITY | all quality checks, exposure top_k=5 | Excess zero days/week ≥1 or ≥1 low-volume week (.4); duplicates ≥.01 (.3); top-customer share >.30 (.3) | Zero-day excess <.5, no low week, duplicates <.002, top-customer share <.10 |
| H_EXTERNAL_CONTEXT | none | Human only | None |

Every template except H_CHANNEL has an unanswered human support criterion (weight .3; external context 1): product/supplier change, fulfilment change, promotion, regular-customer change, key-account change, export problem or external event. No Phase 5 answer is inferred. H_SEGMENT_SHIFT never asserts churn. H_MIX_SHIFT is deferred.

Data confidence is satisfied data-support weight divided by all data-support weight, half-even to four decimals. Human confidence is null; confidence equals data confidence (zero for human-only). Precedence: explicit refute → refuted; human-only → needs_human_context; unanswered human criterion with confidence [.3,.7) → needs_human_context; confidence ≥.7 → supported; confidence <.3 with all required evidence and data support known → refuted; otherwise inconclusive. Root-cause candidate is highest-confidence supported, then catalog order. Model priority affects presentation only.

### 12.4 LLM and structured reports

Strict JSON output models (extra fields forbidden) apply after provider schema validation:

- prioritize_hypotheses: `{ranking:[{template,priority,rationale:[{text,evidence_refs}]}]}`; 1–8 applicable unique entries, priority 1–9, ≤3 rationales, text ≤300 characters, ≤6 refs.
- select_tools: `{calls:[{tool,arguments,hypothesis,purpose}],done:boolean}`; ≤6 discriminated allowlisted calls with §12.2 arguments, applicable hypothesis, purpose ≤200 characters; ≤2 rounds/incident.
- synthesize_findings: `{summary,root_causes:[{hypothesis_id,statement,evidence_refs}],alternatives_considered:[{hypothesis_id,why_not,evidence_refs}],caveats}`; summary ≤600 characters, ≤5 causes, ≤24 alternatives, ≤5 caveats; statements ≤400, other text ≤300; causes require 1–6 refs and supported hypotheses.

Model numbers use `{{ev:<id>.<numeric_field>}}`; entities use approved `{{entity:<kind>:<hash>}}`. Critic rejects digits outside placeholders, unknown/cross-incident refs, absent/nonnumeric fields, raw known entity names, malformed placeholders, markup, unsupported causes and direction/sign contradictions. Tool sanity rejects nonfinite values, impossible counts/shares and invalid decompositions. One repair then deterministic fallback. Source observations are code-generated, separate from hypotheses and narrative.

Rendered segments are `{text}`, `{value,evidence_id,field}` or `{entity,kind}`; numeric display comes from evidence. Incident reports include observations, evidence IDs, hypotheses with status/confidence/criteria/basis, root_cause_hypothesis_id, context_requests and data_gaps. Findings return source, label and rendered summary/causes/alternatives/caveats. **Amended by AMD-14:** the label explicitly identifies AI interpretation, which is separate from deterministic source facts.

### 12.5 Persistence and workflow

Migration 0004 adds tenant tables with business_id, RLS, business-leading indexes and cascading relationships:

| Table | Essential fields / constraints |
|---|---|
| investigations | dataset/revision, status, phase, llm_mode/disabled_reason, graph/catalog versions, last_seq, error_code, starter/timestamps. Status queued/running/completed/completed_no_incidents/insufficient_data/failed; successful terminal rows immutable. |
| investigation_events | investigation, seq, type, actor_kind, payload, dedupe_key, timestamp; unique investigation+seq and investigation+dedupe_key; append-only. |
| hypotheses | investigation/incident, template/catalog version, priority, source, rationale; unique investigation+incident+template. |
| hypothesis_evaluations | hypothesis, status/confidences, criteria, evaluated_at; one/hypothesis, confidence [0,1]. |
| investigation_evidence | investigation+evidence primary key, incident, requested_by; append-only. |
| clarifications | investigation, incident/key, hypothesis, criterion, trigger, question_spec; status open only. |
| findings | investigation, round, content/rendered, source, llm_call_id; unique investigation+round, Phase 5 round 1. |
| llm_calls | investigation, task/attempt, provider/model/prompt_version, input_hash/input/output, stop_reason, token counts, latency, validation/error_code; validation accepted/repaired/invalid/error. |

Graph: initialize → select_incident → generate_hypotheses → select_tools → retrieve_evidence → verify_evidence → detect_missing_context → select_incident; after incidents, synthesize_output → finalize. Evidence selection loops at most once more. No incidents/insufficient data finalize directly. Context triggers/caps follow ARCHITECTURE §7.2 without interrupts.

Durable run_investigation jobs resume PostgreSQL checkpoints, thread_id=investigation ID, synchronous node checkpoints. State contains IDs/counters/validated enum requests, no datasets. Checkpoint DDL is frozen into Alembic; no runtime DDL. Node deduplication prevents duplicate output after restart. Recursion limit 120; model limits 40 calls, 200,000 tokens, 60 seconds/call, 300-second investigation LLM deadline. Transient timeout/429/5xx retries twice, retry-after/backoff capped at ten seconds. Exhaustion opens the per-investigation circuit breaker and switches to deterministic output. Usage and fallback events persist.

### 12.6 API v1

Paths relative to `/api/v1/businesses/{business_id}`. Read permission is investigation:read (all roles); run is investigation:run (owner/analyst). Membership required, cross-tenant IDs return 404.

**Amended by AMD-14:** POST `/investigations` requires `Idempotency-Key` in the §10.1 format. The original 202 response is replayed for 24 hours even after completion. A different body or operation under the same key returns 409 `idempotency_key_reuse`; expired keys are renewed atomically. An active dataset investigation is deduplicated independently of the request key.

| Method/path | Permission | Response |
|---|---|---|
| POST /investigations | run | `{dataset_id:UUID}` →202 summary, binds finished analysis and deduplicates active starts. 409 analysis_not_ready, 429 after 20 starts/business/hour. |
| GET /investigations | read | Optional dataset_id, limit 1–100/default20; newest-first `{items:summary[]}`. |
| GET /investigations/{id} | read | Summary, currency, usage (calls/tokens/fallbacks/evidence), restrictions, incidents, findings. |
| GET /investigations/{id}/events | read | after_seq≥0, limit1–500/default200; ordered `{items:[{seq,type,actor_kind,occurred_at,payload}],last_seq}`. |
| POST /investigations/{id}/retry | run | Failed→202 `{id,status:queued}`, resumes checkpoint; other statuses 409. |
| GET /evidence/{id} | read | ID/kind/revision/incident/query_version, params/params_hash, result/result_hash, integrity boolean, timestamp. |

Summary: id, dataset_id, revision_id, status, phase, llm_mode/disabled_reason, error_code, graph/catalog versions, last_seq, created/started/completed timestamps. No answering, cancellation, human review or external-write endpoints in Phase 5.

**AMD-13 budget and execution refinement:** successful calls record provider-reported token usage. Failures or absent usage charge the UTF-8 bytes of input and schema, 1,024 bytes of protocol overhead and the requested output reservation. Retries and repair attempts consume this same persisted budget. The entire workflow has a 600-second runtime deadline, including deterministic tools and database work, and a per-investigation PostgreSQL advisory lock serializes concurrent workers.
