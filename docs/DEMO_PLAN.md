# ROOT 2.0 — Six-Minute Judging Demonstration

> Baseline created in Phase 1. The demo shows the **real system on generated data**. No step relies on a scripted or hardcoded outcome.
> If live output differs from the expectations below, the presenter shows the actual output and uses the matching branch in §6. The system is never patched to match the script.

> **Current implementation note (AMD-21, Phase 10):** this document's original six-minute script remains a baseline, not a claim that every listed capability exists. The implemented journey is CSV upload → deterministic analysis → investigation → human clarification and interpretation versions → immutable recovery-plan revisions → owner approval/rejection → explicitly requested Google Sheets task export and read-back verification. Replay shows persisted versions/events. Correction overlays, hypothesis rejection/findings-review loops, sequence scrubbing, Slack, account provisioning UI, retention/deletion and production deployment are deferred by the recorded amendments. Use the implemented journey in `apps/web/e2e/workflow.spec.ts`; do not demonstrate the deferred steps as working features.

## 1. Objectives

In six minutes, show that:

1. ROOT finds material problems autonomously from raw CSVs (J4).
2. Every conclusion traces to computed evidence (J1, J3).
3. Human input **changes** the investigation: context reroutes it, a correction creates a new revision, constraints shape the plan (J5).
4. Only approved actions execute, exactly once, in a real Google Sheet (J1, J2).
5. Replay shows what the AI believed before and after each human input (J3, J5).

## 2. Setup (done before judging and verified in the rehearsal checklist §7)

| Item | How it is prepared |
|---|---|
| Production deployment | Phase 13. `/readyz` green |
| Demo account | Owner `demo-owner` and analyst `demo-analyst` registered through the normal UI |
| Demo business | Created through the UI. Currency USD |
| Google Sheets | Connected and verified through the normal flow (ARCHITECTURE §11.2). Sheet open in a second browser tab |
| Slack (optional) | Webhook connected to a demo channel, visible in a third tab |
| Dataset files | Generated: `generate_dataset.py --seed <recorded> --weeks 26 --customers 2500 --products 120 --scenario quality_defect --scenario test_orders --scenario fulfillment_delay` plus 1% malformed rows and one PII-like column added by the generator's `--noise` option. Files are kept locally and **uploaded live** |
| LLM | Real provider configured. Budget headroom checked |
| Fallback assets | Screen recording of a full rehearsal. Second business with a completed investigation (for Replay if time runs short) |

## 3. Narrative

"A small retailer's revenue and margin slipped. Their data is in four spreadsheets. ROOT investigates like an analyst, asks the owner what only the owner knows, fixes its own understanding when told the data is wrong, and executes only what the owner approves."

## 4. Script and architecture mapping

| # | Time | Presenter action | Expected system behaviour | Capability (API / UI) | Req |
|---|---|---|---|---|---|
| 1 | 0:00–0:15 | Sign in as owner and open the demo business | Business overview | `POST /auth/login`, `GET /auth/me` | FR-01, FR-02 |
| 2 | 0:15–0:45 | Upload the four CSVs | 202 → validating → `ready`. Report shows quarantined rows by code (e.g. V-030, V-014) and V-041 "PII column dropped" | `POST /datasets`, `GET /datasets/{id}`, `GET /issues` | FR-03, FR-04, SEC-22 |
| 3 | 0:45–1:00 | Glance at the dashboard | Weekly net revenue, refund rate, margin, segment distribution, chart tables | `GET /metrics`, `GET /segments` | FR-05, FR-06, FR-27 |
| 4 | 1:00–1:40 | Click **Start investigation** | Live timeline: revision created → metrics → incidents ranked by impact (expected: refund-rate incident on the defect category, net-revenue incident distorted by test orders) | `POST /investigations`, `GET /events?after_seq` | FR-07, FR-26 |
| 5 | 1:40–2:10 | Open incident #1 | Competing hypotheses (`H_RETURNS_QUALITY`, `H_FULFILLMENT`, `H_DATA_QUALITY`, `H_EXTERNAL_CONTEXT`). Evidence cards (reason-mix shift, contribution by product), each with tool, params and hash | `GET /incidents`, `/hypotheses`, `/evidence/{id}` | FR-08, FR-09, FR-10, FR-16 |
| 6 | 2:10–2:40 | Clarification appears. Answer "Yes, a supplier or batch change" plus the date range | Context evidence added. `H_RETURNS_QUALITY` confidence rises (data vs human split shown). Rival re-evaluated. The graph moves on | `POST /clarifications/{id}/answer` → resume job | FR-11, FR-12 |
| 7 | 2:40–3:20 | Second clarification (CT-3): "One customer accounts for a large share of baseline revenue. Are these legitimate?" Select "Internal test orders" → exclude that customer's records | New overlay v1 → new revision. Revenue incident recomputed. **Revision diff** shows the impact change. The original revision is untouched | `POST /corrections`, `GET /revisions/compare` | FR-13, FR-18 |
| 8 | 3:20–3:40 | Reject one remaining hypothesis with a reason, then **Accept** findings | Rejected hypothesis is excluded from the root cause. The findings narrative cites evidence, and numbers render from evidence | `POST /hypotheses/{id}/reject`, `POST /findings/{id}/review` | FR-14, FR-15, FR-16 |
| 9 | 3:40–4:15 | Set constraints (max discount 10%, budget, 4-week horizon). Edit one assumption (response rate) | Plan with strategies, actions, and low/mid/high estimates labelled as assumption-driven | `POST /recovery-plans/{id}/constraints`, `GET /recovery-plans/{id}` | FR-19 |
| 10 | 4:15–4:45 | Edit a tracker task's text. **Reject** the Slack action. **Approve** the plan export and the task tracker | Edit changes the payload hash. Rejected action shows "no execution". Approvals queue execution | `PATCH /actions/{id}`, `/reject`, `/approve` | FR-20, SEC-13 |
| 11 | 4:45–5:15 | Switch to the Google Sheet tab | New `ROOT Plan …` tab and appended `ROOT Tasks` rows appear. The execution log shows one attempt each | executor, `GET /executions` | FR-21, FR-24 |
| 12 | 5:15–5:45 | Open **Replay** and scrub to before step 6, then before step 7 | Side-by-side belief before and after: hypothesis status and confidence changes, revision switch and impact delta, linked to the human event | `GET /replay?at_seq`, `GET /human-impact/{seq}` | FR-17 |
| 13 | 5:45–5:55 | Open the audit log | Entries for upload, answers, correction, approvals, rejection, executions | `GET /audit-log` | FR-25 |
| 14 | 5:55–6:00 | Closing line | — | — | — |

## 5. Timing budget and controls

- **System latency budget:** step 4 ≤ 40 s to the first clarification, measured in rehearsals (NFR-08 ≤ 60 s). Steps 6 and 7 resume ≤ 15 s each. Execution ≤ 10 s.
- **Talk track while waiting:** at step 4 the presenter narrates the timeline events as they appear.
- **Hard cut rules:**
  - At 3:40 still on step 7: skip step 8's rejection and accept findings directly.
  - At 5:15 still on step 11: skip to step 12 using the fallback business.

## 6. Branches and failure handling (honest fallbacks)

| Situation | Branch |
|---|---|
| Live incidents differ from the expectation | Investigate whatever ranks #1. The flow (clarify → correct → plan → approve) is the same for any incident |
| No CT-3 clarification is raised | Use the correction button on the incident's top-customer evidence card (same API, step 7) |
| LLM slow or unavailable | The investigation continues in deterministic mode (labelled). Say so: this demonstrates NFR-03 |
| Sheets error | The action shows `failed` with a reason. Click **Retry** (owner). If still failing, show the recoverable state and switch to the recording for the sheet view |
| Network or deploy failure | Play the rehearsal recording and narrate. State plainly that it is a recording |

## 7. Rehearsal checklist (Phase 13, three timed runs recorded in the engineering log)

- [ ] `/readyz` green. Error-free logs for the previous 24 h.
- [ ] Demo files regenerated from the recorded seed and their checksums match.
- [ ] Sheets integration `verified`. The test tab from rehearsal deleted.
- [ ] LLM key valid. Budget checked.
- [ ] Browser zoom at 110%. Notifications off. Tabs open: app, sheet, (Slack).
- [ ] Fallback business has a completed investigation. Recording file opened.
- [ ] Each run timed ≤ 6:00. Timings recorded.
