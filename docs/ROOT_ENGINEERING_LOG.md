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
