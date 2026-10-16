# AGENTS.md — Mandatory rules for coding-agent sessions on ROOT 2.0

Every coding-agent session (any tool, any model) working in this repository **must** follow these rules. If a rule conflicts with a user instruction, stop and ask the user. Do not silently pick one.

## 1. Before you start

1. Read, in order: this file, `docs/ROOT_MASTER_PLAN.md` (including §13 Amendments), the latest entries in `docs/ROOT_ENGINEERING_LOG.md`, and the documents referenced by your phase.
2. Identify the **single phase** you are executing. Confirm in the log that all previous phases are complete and that their acceptance criteria were met.
3. Run `git status` and `git log --oneline -5`. Never discard or overwrite uncommitted work you did not create.

## 2. Rules

| ID | Rule |
|---|---|
| R-1 | Work only within your phase's scope. Items listed as out of scope or belonging to later phases must not be started, scaffolded or stubbed, except stubs the phase prompt explicitly requires |
| R-2 | Follow the contracts in `docs/DATA_CONTRACTS.md`, `docs/ARCHITECTURE.md` and `docs/SECURITY_MODEL.md` exactly. If one is wrong or insufficient, append an amendment to `ROOT_MASTER_PLAN.md §13` (and update the affected contract document in a clearly marked "Amended by AMD-n" note) **before** writing divergent code |
| R-3 | Phases are strictly sequential. Do not begin phase N+1 in the same session unless the user explicitly asks *and* phase N's acceptance criteria are all met and logged |
| R-4 | `docs/ROOT_MASTER_PLAN.md` is immutable to history. Only append to §13. `docs/ROOT_ENGINEERING_LOG.md` is append-only: add a new phase section and never edit earlier sections, except to fill in a "pending" commit hash or push status left by the immediately previous phase, and note that you did so |
| R-5 | Never claim a test passed unless you executed it in this session. Record exact commands, counts and failures in the log. Tests not run are listed as **not run** with the reason |
| R-6 | Never weaken, skip, `xfail`, or delete a test, or lower a coverage or quality threshold, to make CI pass. Fix the defect, or record a blocker and stop |
| R-7 | Never hardcode credentials, secrets, sample results, investigation outcomes, demo entity names or demo dates in runtime code. Runtime code must not import `backend/scripts/` |
| R-8 | Add a dependency only when code in the current phase uses it, and only if it is listed in ARCHITECTURE §13.1 or justified by an amendment. Pin exact versions verified against the official registry at install time. No Redis, Kafka, vector DBs, microservices or extra frameworks |
| R-9 | Strict typing: mypy `--strict` (Python) and TypeScript `strict`. No `Any` or `as any` without a justifying comment. No `# type: ignore` without an error code and a reason |
| R-10 | Business calculations live in deterministic modules (`analytics/`, `investigation/verification.py`, `recovery/estimator.py`). The LLM never produces numbers or entity names that are shown as facts (placeholder contract). The LLM never gets a tool with side effects |
| R-11 | All database access goes through the tenant context. Every new tenant table needs `business_id`, RLS enabled and forced, and inclusion in tests T-17 and T-18. Every new route declares a permission (T-16 enforces this) |
| R-12 | SQL uses bound parameters only. Identifiers come from server-side allowlists |
| R-13 | External effects happen only in the action executor, only for an owner-approved payload hash, and only after re-checking authorization. Every new action type needs an idempotency marker and reconciliation (or `outcome_unknown`) design, documented before implementation |
| R-14 | Schema changes go through Alembic, follow expand → migrate → contract, and must be reversible by redeploying the previous release |
| R-15 | Secrets come only from environment variables. Never print, log or commit them. `.env*` files other than `.env.example` must stay git-ignored |
| R-16 | Accessibility: WCAG 2.2 AA. Every chart has a table alternative. All flows are keyboard-operable |
| R-17 | Keep it simple. No speculative abstraction, unused code or "future" hooks |

## 3. Git and GitHub

- Commit with a descriptive conventional message (`docs:`, `feat:`, `fix:`, `test:`, `chore:`) that references the phase.
- Commits are authored by the repository owner's configured git identity. **Do not add AI co-author or "Generated with" trailers, and do not add any AI agent as a repository collaborator.**
- Push without force. Never rewrite published history and never force-push.
- Verify a push with `git ls-remote origin <branch>` and compare it with `git rev-parse HEAD`. Record the result in the log.
- If a push fails (no remote, no auth), record the exact error and the commands needed. Never claim success.

## 4. Finishing a phase

1. All acceptance criteria are met, with evidence.
2. The full test suite has been run, with results recorded.
3. A log section has been appended using the template in `docs/ROOT_ENGINEERING_LOG.md`.
4. Committed and pushed (or the blocker documented).
5. Final report to the user: what changed, test results, unresolved risks, commit hash and push status.
