# ROOT 2.0 — Security Model

> Baseline created in Phase 1. Aligned with OWASP ASVS 4.0 (Level 2 as the target), OWASP Top 10 (2021) and the OWASP Top 10 for LLM Applications.
> Every SEC requirement maps to tests in [TEST_STRATEGY.md §5](TEST_STRATEGY.md) and phases in [ROOT_MASTER_PLAN.md §10.3](ROOT_MASTER_PLAN.md).

## 1. Security objectives

1. **Confidentiality:** a business's data is visible only to its members, according to role.
2. **Integrity:** evidence, datasets, audit records and approvals cannot be altered silently. Facts are not fabricated by the LLM.
3. **Authorization of effects:** no external side effect happens without an owner approving the exact payload.
4. **Availability:** abuse cannot exhaust the service, and failures are recoverable.
5. **Accountability:** every security-relevant and decision event is attributable and retained.

## 2. Assets and trust boundaries

| Asset | Sensitivity |
|---|---|
| Business sales, customer and returns data | Confidential (commercial). No direct PII by contract (SEC-22) |
| Session tokens, CSRF secrets | Secret |
| Password hashes | Secret |
| Google service-account key, Slack webhook URLs, LLM API key, encryption keys | Secret |
| Evidence, findings, approvals, audit log | Integrity-critical |
| Google Sheets of tenants | External, tenant-owned |

| Boundary | Between | Primary controls |
|---|---|---|
| TB-1 | Browser ↔ Vercel/Next.js | TLS, CSP, cookies, output encoding |
| TB-2 | Next.js rewrite ↔ FastAPI | TLS, origin checks, backend is authoritative for all authn/authz |
| TB-3 | FastAPI ↔ PostgreSQL | TLS, least-privilege roles, RLS |
| TB-4 | FastAPI ↔ LLM provider | API key, data minimisation, output validation |
| TB-5 | FastAPI ↔ Google Sheets / Slack | Service-account scope, ownership verification, URL allowlist, payload-hash approval |
| TB-6 | Uploaded CSV / human free text → system | Strict parsing, size limits, untrusted-data handling |

## 3. Security requirements

| ID | Requirement | ASVS / OWASP reference |
|---|---|---|
| SEC-01 | Passwords hashed with Argon2id (argon2-cffi defaults or stronger: memory ≥ 19 MiB, t ≥ 2). Length 12–128. Must not equal the email. No composition rules. Rehash on parameter change | ASVS V2.1, V2.4 |
| SEC-02 | Opaque 256-bit session tokens, stored hashed. Cookie `__Host-root_session`, `HttpOnly; Secure; SameSite=Lax; Path=/`. Rotated on login. Idle timeout 8 h, absolute timeout 7 days. Server-side revocation on logout, password change and membership removal (sessions keep working for other businesses) | ASVS V3 |
| SEC-03 | CSRF: synchronizer token bound to the session (HMAC of the session secret), sent in the `X-CSRF-Token` header for unsafe methods. Plus `SameSite=Lax`. Plus an `Origin`/`Referer` check against `FRONTEND_ORIGIN` | ASVS V4.2.2, V13.2.3 |
| SEC-04 | Login throttling: 5 failures per account per 15 min → 429. 20 failures per IP per 15 min → 429. Uniform error message and similar timing for unknown users (dummy hash verify) | ASVS V2.2 |
| SEC-05 | Deny-by-default RBAC. Every route declares a permission (§5). Checks happen server-side in a dependency. The UI hides controls but is never trusted | ASVS V4.1, A01 |
| SEC-06 | Tenant isolation: every query is scoped by `business_id` from a verified membership. Postgres RLS (FORCE) is the second layer. Cross-tenant access → 404 | ASVS V4.2.1, A01 |
| SEC-07 | Input validation: Pydantic strict models (`extra="forbid"`), body limits (1 MB JSON, 60 MB multipart), CSV rules (DATA_CONTRACTS §2), filename sanitisation, content sniffing (no NUL, valid UTF-8) | ASVS V5.1, V12.1 |
| SEC-08 | SQL injection prevention: bound parameters only. Identifiers (dimensions, metrics, sort) come only from server-side allowlist maps. Raw string SQL formatting is banned by lint (ruff `S608`) and review | ASVS V5.3.4, A03 |
| SEC-09 | XSS prevention: React escaping only, no `dangerouslySetInnerHTML` (ESLint rule), LLM text rendered as plain text, strict CSP | ASVS V5.3.3, A03 |
| SEC-10 | Security headers on all responses (§7) | ASVS V14.4 |
| SEC-11 | Secrets come only from environment / platform secret stores. Integration secrets are encrypted at rest (AES-256-GCM, key ID for rotation). They are never returned by the API or logged. Verification codes are stored hashed | ASVS V6.4, V8.3 |
| SEC-12 | LLM boundary: no side-effecting tools, tool and parameter allowlists, untrusted text isolated in data fields, strict output schemas, placeholder-only numbers and entities, call and token budgets, full call records | OWASP LLM01, LLM02, LLM06, LLM08 |
| SEC-13 | Action authorization: only `owner` approves. Approval is bound to the payload hash. Edits invalidate approval. Authorization is re-checked before each external call. Rejected and revoked actions have no execution path | ASVS V4.1, A04 |
| SEC-14 | Idempotency: `Idempotency-Key` on critical POSTs, dedupe keys on jobs, idempotency markers on external writes, `outcome_unknown` for non-reconcilable effects | A04 |
| SEC-15 | SSRF prevention: the backend makes outbound requests only to the configured LLM provider (Anthropic or, per AMD-16, `api.groq.com`), Google APIs (official client) and Slack webhooks matching `^https://hooks\.slack\.com/services/`. Redirects are disabled for Slack calls. No user-supplied URL is fetched otherwise | ASVS V12.6, A10 |
| SEC-16 | Spreadsheet formula-injection prevention: `valueInputOption=RAW` and a `'` prefix for cells starting with `= + - @ \t \r`. Applies to any CSV export too | OWASP CSV Injection |
| SEC-17 | Spreadsheet ownership proof (verification code in `ROOT_VERIFY!A1`) and a global unique binding of a spreadsheet to one business | A01 |
| SEC-18 | Append-only audit log (DB trigger and privileges). It covers auth events, membership changes, uploads, deletions, integration changes, every HITL input, approvals, rejections, executions and permission denials | ASVS V7.1, V7.2, A09 |
| SEC-19 | Error handling: RFC 9457 responses with no stack traces, SQL, paths or library versions. Internal details are logged with `request_id` | ASVS V7.4, A05 |
| SEC-20 | Rate limits: auth (SEC-04). Uploads 10 per business per hour. Investigation starts 20 per business per hour. Integration tests 5 per hour. Global per-session 300 requests/min. Postgres-backed for auth and business quotas, in-process token bucket for the global limit (single instance, documented) | ASVS V11.1, A04 |
| SEC-21 | Supply chain: lockfiles committed, exact pins, pip-audit and npm audit gates, gitleaks on every push, GitHub Dependabot alerts enabled, no `curl \| sh` in builds, Actions pinned by commit SHA | A06, A08 |
| SEC-22 | Data minimisation: no direct PII columns stored. The LLM receives aggregates only (no customer IDs, no raw rows). Logs carry no business data | ASVS V8.3, GDPR art. 5(1)(c) principle |
| SEC-23 | Retention and deletion per §10. Business deletion purges all tenant rows and checkpoints | ASVS V8.3.8 |
| SEC-24 | Logging hygiene: structured logs with a redaction filter (cookies, `Authorization`, `X-CSRF-Token`, secrets, webhook URLs, passwords, verification codes). Tests assert the absence of sentinel secrets | ASVS V7.1.1, V7.1.2 |
| SEC-25 | Origin and CORS: the backend serves same-origin through the proxy. CORS is disabled except for `FRONTEND_ORIGIN` with credentials. `Origin` is checked on unsafe methods | ASVS V14.5.3 |

> **Amended by AMD-18 (Phase 8):** SEC-15: Google APIs are reached through `httpx` (not the discovery client) at two constant hosts, `oauth2.googleapis.com` and `sheets.googleapis.com`; the key file's own token URI is ignored. SEC-13: execution is a separate owner request after approval; re-authorization happens before every write.

## 4. Authentication and sessions

### 4.1 Registration and login

> **Amended by AMD-4:** email is lowercase text with a CHECK and unique index, not `citext`. The unused session user-agent/IP/CSRF-secret fields and user lockout counters are omitted; `auth_attempts` is the throttle source. Registration and membership lifecycle remain deferred under AMD-8.

- Email is normalised (trim, lowercase) and stored as `citext`. The password is checked for length and against the email. Argon2id hash.
- Login: look up the user. If absent, verify against a dummy hash (equal timing). On success, revoke any pre-auth session, create a new session token (`secrets.token_urlsafe(32)`), store `sha256(token)`, set the cookie, and record an audit entry.

### 4.2 Session lifecycle

- `last_seen_at` is updated at most once per minute.
- A session expires at idle 8 h or absolute 7 days.
- Logout sets `revoked_at` and clears the cookie.
- Sessions are bound loosely to a `user_agent_hash`: a change is logged but not enforced, to avoid false lockouts.

### 4.3 CSRF

> **Amended by AMD-4:** use the signed double-submit nonce cookie and session-bound HMAC defined in ROOT_MASTER_PLAN §13 AMD-4; the formula below is historical baseline only.

`GET /auth/csrf` returns `token = base64(HMAC_SHA256(session.csrf_secret, session_id_hash))`. Pre-login, a temporary secret in a `__Host-root_csrf` cookie is used. The token is checked in constant time on POST, PUT, PATCH and DELETE. A missing or invalid token → 403 `csrf_failed`.

## 5. Authorization model

### 5.1 Permissions per role

| Permission | owner | analyst | viewer |
|---|:-:|:-:|:-:|
| `business:read` | ✓ | ✓ | ✓ |
| `business:update` | ✓ | | |
| `business:delete` | ✓ | | |
| `member:read` | ✓ | ✓ | |
| `member:manage` | ✓ | | |
| `dataset:read` | ✓ | ✓ | ✓ |
| `dataset:upload` | ✓ | ✓ | |
| `dataset:delete` | ✓ | | |
| `analytics:read` | ✓ | ✓ | ✓ |
| `investigation:read` | ✓ | ✓ | ✓ |
| `investigation:run` | ✓ | ✓ | |
| `investigation:cancel` | ✓ | ✓ | |
| `hitl:answer` | ✓ | ✓ | |
| `action:edit` | ✓ | ✓ | |
| `action:reject` | ✓ | ✓ | |
| `action:approve` | ✓ | | |
| `action:retry` | ✓ | | |
| `integration:read` | ✓ | ✓ | |
| `integration:manage` | ✓ | | |
| `action:execute` (AMD-18) | ✓ | | |
| `audit:read` | ✓ | | |

> **Amended by AMD-10 (implemented in Phase 4):** `analysis:run` exists, granted to owner and analyst. `analytics:read` is granted to owner, analyst and viewer.

> **Amended by AMD-12 (implemented in Phase 5):** `investigation:read` and `investigation:run` exist as listed. `investigation:cancel` is not yet implemented.

### 5.2 Enforcement

> **Amended by AMD-15 (implemented in Phase 6):** `hitl:answer` exists, granted to owner and analyst. Answers additionally require a clarification of that investigation in the caller's business and the paused `checkpoint_id`; free text is untrusted data (stored, attributed, shown as plain text, never parsed, never rendered through placeholders, never sent to the LLM) and is never an authorization.

- Each FastAPI route declares `Depends(require_permission("…"))`. A startup check and test T-16 fail if any `/api/v1/businesses/**` route lacks one.
- The resource → business check is `SELECT … WHERE id = :id AND business_id = :bid`. A miss returns 404.
- The LLM and the graph run with the investigating business's tenant context and **no user permissions**. They cannot call approval or execution code. That code lives in `recovery/approvals.py` and `jobs` handlers, which are reachable only from authenticated API routes and the executor.

## 6. Tenant isolation

> **Amended by AMD-3:** runtime RLS is ENABLE rather than FORCE, with readiness refusing owner, superuser or BYPASSRLS roles. Tenant policies use `NULLIF(current_setting('app.current_business_id', true), '')::uuid`. **AMD-12/AMD-13:** checkpoint tables have no RLS and are worker-only, accessed through a tenant-scoped investigation ID, a per-investigation advisory lock and a whole-run timeout. Imported business text never enters Phase 5 model inputs; tools expose only aggregate facts and opaque references.

- **Database roles:**
  - `root_owner` owns the schema and runs migrations.
  - `root_app` is used at runtime: `NOSUPERUSER NOBYPASSRLS`, DML only on application tables, no UPDATE/DELETE on insert-only tables (`audit_log`, `investigation_events`, `evidence_items`, `approvals`, `data_overlays`, dataset row tables), and no DDL.
- **RLS:** `ALTER TABLE … ENABLE ROW LEVEL SECURITY; ALTER TABLE … FORCE ROW LEVEL SECURITY;` with the policy `USING (business_id = current_setting('app.current_business_id', true)::uuid) WITH CHECK (same)`. `businesses` and `memberships` have policies based on `app.current_user_id`.
- **Tenant context:** set per transaction with `SET LOCAL` (pool-safe). If missing, `current_setting(..., true)` returns NULL, so no rows match.
- **Global uniqueness** (`integrations.spreadsheet_id`) is checked through a `SECURITY DEFINER` function that returns only a boolean, which avoids leaking the other tenant.
- **LangGraph checkpoint schema:** accessed only by the worker with thread IDs from tenant-scoped investigation rows. Checkpoints contain IDs only.

## 7. Browser and transport security

Backend responses and the Next.js `headers()` set:

- `Content-Security-Policy: default-src 'self'; script-src 'self' 'nonce-…'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'`. Nonce-based CSP is set via Next.js middleware. `style-src 'unsafe-inline'` is required by Recharts and Radix inline styles and is the documented residual risk.
- `Strict-Transport-Security: max-age=63072000; includeSubDomains` (production)
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- `Cross-Origin-Opener-Policy: same-origin`
- API responses also send `Cache-Control: no-store`.

TLS is terminated by Vercel and Railway. Database connections use `sslmode=require` in production.

## 8. Threat model (STRIDE)

| # | Threat | Boundary | STRIDE | Control(s) | Test |
|---|---|---|---|---|---|
| TH-01 | Credential stuffing or brute force | TB-1 | S | SEC-04, SEC-01 | T-15 |
| TH-02 | Session hijack via XSS | TB-1 | S, I | HttpOnly cookie, CSP, no raw HTML (SEC-02, SEC-09) | T-13, T-20 |
| TH-03 | CSRF on approve or upload | TB-1 | T | SEC-03 | T-14 |
| TH-04 | IDOR across businesses | TB-2 | I, E | SEC-05, SEC-06 | T-16, T-17, T-18 |
| TH-05 | Privilege escalation (analyst approves) | TB-2 | E | SEC-05, SEC-13 | T-16, T-32 |
| TH-06 | SQL injection via dimension, filter or sort | TB-3 | T, I | SEC-08 | T-19, T-07 |
| TH-07 | Malicious CSV (oversize, encoding tricks, formula payloads) | TB-6 | D, T | SEC-07, SEC-16 | T-10, T-35 |
| TH-08 | Prompt injection via `reason_text` or human free text aiming to trigger actions, leak data or distort conclusions | TB-4, TB-6 | T, E, I | SEC-12 (no side-effect tools, schemas, placeholders), ADR-009 | T-22, T-23 |
| TH-09 | LLM hallucinated numbers presented as facts | TB-4 | T | ADR-004 placeholder contract | T-23 |
| TH-10 | Executing a modified action after approval | TB-5 | T, E | Payload-hash binding, re-check before execution | T-32 |
| TH-11 | Duplicate external writes on retry or crash | TB-5 | T | SEC-14 | T-34 |
| TH-12 | Tenant writes into another tenant's spreadsheet | TB-5 | E | SEC-17 | T-36 |
| TH-13 | SSRF via the Slack webhook field | TB-5 | I, E | SEC-15 | T-37 |
| TH-14 | Secret leakage in logs, responses or the repository | all | I | SEC-11, SEC-24, SEC-21 (gitleaks) | T-21, T-41 |
| TH-15 | Audit tampering or repudiation of approvals | TB-3 | R, T | SEC-18, insert-only approvals | T-38 |
| TH-16 | Resource exhaustion (large uploads, investigation spam, LLM cost) | TB-1, TB-4 | D | SEC-20, NFR-10 budgets, size limits | T-40, T-50, T-10 |
| TH-17 | Error responses leak internals | TB-2 | I | SEC-19 | T-39 |
| TH-18 | Dependency compromise | build | T | SEC-21 | T-41 |
| TH-19 | Human correction used to silently erase inconvenient evidence | TB-2 | T, R | ADR-007 overlays, insert-only evidence, revision compare, audit | T-27, T-38 |
| TH-20 | PII exposure through uploads or the LLM | TB-6, TB-4 | I | SEC-22 | T-11, T-22 |
| TH-21 | Cross-origin abuse of the API | TB-2 | S | SEC-25 | T-47 |
| TH-22 | Data retained beyond need after deletion | TB-3 | I | SEC-23 | T-42 |

**LLM-specific notes:**

- The LLM's maximum capability is limited to choosing among catalog entries, phrasing text inside schema fields, and calling read-only evidence tools with validated parameters scoped to the current tenant and revision.
- Successful injection can therefore at worst mis-prioritise hypotheses or produce misleading phrasing. That is visible, evidence-linked, reviewed by a human at findings review, and cannot change numbers, statuses (which are deterministic) or actions.

## 9. Audit logging

- The writer is `audit/writer.py` and runs in the same transaction as the action.
- Each entry records: actor, business, action (namespaced, e.g. `action.approved`), target, request_id, truncated IP prefix (/24 IPv4, /48 IPv6) and metadata. Metadata never includes secrets or business data values.
- **Events covered:**
  - auth: register, login success and failure, logout, lockout
  - membership: add, change, remove
  - business: create, update, delete
  - dataset: upload, ready, rejected, delete
  - investigation: start, cancel, retry
  - every HITL input
  - plan constraints
  - action: edit, approve, reject, revoke, retry, resolve, execution outcome
  - integration: create, verify, revoke, test
  - every 403 and every cross-tenant 404 (the latter tagged `authz.denied`)
- Owners read the log through the API. There is no API to modify it.

## 10. Data retention and deletion

| Data | Retention | Mechanism |
|---|---|---|
| Raw uploaded files (`dataset_files.raw`) | 30 days after upload | `retention_sweep` job sets raw to NULL and `purged_at` |
| Normalised dataset rows, analyses, investigations, evidence, events | Until the dataset or business is deleted | — |
| Validation issue excerpts | Same as the dataset | — |
| `llm_calls.input/output` | 90 days, then nulled (hashes and metadata kept) | retention sweep |
| Sessions | Deleted 30 days after expiry or revocation | retention sweep |
| `auth_attempts` | 30 days | retention sweep |
| `idempotency_keys` | 24 h | retention sweep |
| `jobs` (terminal) | 30 days | retention sweep |
| Audit log | 365 days, then deleted by the owner-role maintenance job (the only DELETE path, executed as `root_owner`) | scheduled maintenance |
| Business deletion | Immediate soft lock, purge job within 24 h: cascade delete of all tenant rows and LangGraph checkpoints for its investigation thread IDs | One tombstone audit entry (`business.deleted`, no data) is kept 365 days |
| Dataset deletion | Allowed when no non-terminal investigation uses it. Deletes rows and dependent revisions and analyses. Completed investigations on it are deleted too, after an explicit confirmation listing them | — |

Data in tenants' Google Sheets and Slack channels belongs to the tenant and is not deleted by ROOT. The UI states this at approval time.

## 11. Secrets management

| Secret | Location | Rotation |
|---|---|---|
| `SESSION_SECRET` | Railway env | Rotation invalidates CSRF tokens. Sessions remain (token hashes are independent) |
| `INTEGRATION_ENCRYPTION_KEY` (+ `_ID`) | Railway env | New key ID. Old ciphertexts are decrypted with the old key and lazily re-encrypted. Old keys are kept in `INTEGRATION_ENCRYPTION_KEYS_OLD` until re-encryption completes |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Railway env (base64) | Create a new key in GCP, deploy, then delete the old key |
| `LLM_API_KEY` | Railway env | Provider console |
| DB credentials | Railway-managed `DATABASE_URL` (app role) and `DATABASE_URL_MIGRATIONS` (owner role) | Platform rotation |

Rules: `.env` files are git-ignored. `.env.example` contains names only. No secret appears in the frontend bundle or uses a `NEXT_PUBLIC_` prefix. CI uses GitHub encrypted secrets only for deployment jobs, never for pull requests from forks.

## 12. Security review checklist (executed in Phase 12)

1. T-16 route-permission coverage is at 100%.
2. T-17 and T-18 are green for every tenant route.
3. CSP, HSTS and other headers are verified on the production URL.
4. gitleaks full history is clean, and pip-audit and npm audit are clean.
5. The prompt-injection corpus (T-22) passes with ≥ 20 cases.
6. Payload-hash approval and rejected-action tests (T-32, T-33) are green.
7. Idempotency scenarios (T-34) are green.
8. Sentinel-secret log scan (T-21) is clean.
9. A manual review of every raw SQL string confirms no string formatting with input.
10. Database role privileges are verified (`\dp`), and `root_app` has no BYPASSRLS or DDL.
11. Retention sweep and business deletion are verified (T-42).
12. Error-response leakage check passes (T-39).

## 13. Residual risks

| Risk | Rationale | Owner phase |
|---|---|---|
| No MFA or SSO | Non-goal NG-09. Mitigated by throttling and strong hashing | Post-competition |
| `style-src 'unsafe-inline'` | Required by chart and UI libraries. Script CSP stays strict | 12 |
| Global rate limit is per instance | Single instance by design. Auth and business quotas are DB-backed | 12 |
| The service-account email is shared across tenants | Mitigated by ownership verification and unique binding | 11 |
| LLM provider sees aggregates | Data minimisation. Choose a provider with no-training-on-API-data terms (verified in Phase 7) | 7 |
| Slack webhook effects are non-idempotent | `outcome_unknown` with human resolution | 11 |
