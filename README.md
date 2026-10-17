# Biztective

Find out why your business numbers changed, then decide what to do.

## The problem

When revenue or margin slips, owners of small businesses get charts but no explanation. Finding the cause means digging through spreadsheets by hand, and the answer often depends on context only the owner knows. Plans made from guesses are hard to trust and hard to check.

## The solution

Biztective takes four CSV files (or a read-only PostgreSQL database), checks them, and finds the biggest changes between periods with fixed, repeatable rules. It then investigates each change, asks the owner the one question the data cannot answer, and records a new version of its findings. Every figure links back to the source records behind it.

## How it works

1. Sign in and open your business. The Business health page shows the headline numbers.
2. Upload orders, products, customers and returns CSV files, or connect a read-only PostgreSQL database. Each upload or sync becomes a checked, versioned dataset.
3. Biztective validates the data, lists any rejected rows with the rule and reason, and computes the current period against the previous one.
4. It ranks the biggest changes (incidents) by estimated impact and attaches the evidence for each.
5. You start an investigation. It compares explanations, then pauses on the most useful question. Your answer resumes the saved workflow and produces a new findings version. Investigation replay compares versions.
6. From a completed investigation you can create recovery plans, approve or reject them, and export approved plans to a Google Sheet (requires server configuration).

## Key features

- Compare the latest four complete weeks against the twelve before, with every incident's impact and evidence.
- Validate uploads and report each rejected row with its rule code and reason.
- Rearrange the dashboard by mouse, touch long-press or keyboard, with the layout saved per business in the browser.
- Ask one clarifying question at a time and store the answer with its author.
- Replay any two investigation versions to see what changed and that the original measurements did not.
- Sync from a read-only PostgreSQL source without storing its password.
- Switch between light and dark themes, with the choice remembered.
- Show data limitations next to the figures they affect.

## Tech stack

| Layer | Tool |
|---|---|
| Frontend | Next.js 16.4.2, React 19, TypeScript |
| Styling and components | Tailwind CSS v4, shadcn/ui on Base UI, lucide-react |
| Animation | motion 14 |
| Charts | Recharts 3.10, plus small hand-built SVG and bar charts |
| Backend | FastAPI, Python 3.14, uv |
| Data access | SQLAlchemy, Alembic migrations |
| Database | PostgreSQL 18 with row-level security |
| Investigation workflow | LangGraph checkpointed to PostgreSQL; optional Anthropic or Groq models |
| Tests | Vitest and Testing Library, Playwright with axe, pytest |
| Packaging | Docker (local database, production images) |

## Project structure

```
apps/web/                  Next.js frontend
  src/app/                 Routes: login, business pages, datasets, investigations, decisions, integrations
  src/components/          Page building blocks, health widgets, navigation, investigation and recovery panels
  src/components/ui/       Buttons, menus, draggable widget grid, theme toggle, animated sign-in
  src/lib/                 API client, formatting, health and status helpers
  tests/                   Unit and component tests (Vitest)
  e2e/                     Browser tests (Playwright)
  public/                  Logo
services/api/              FastAPI backend
  app/                     auth, tenancy, ingestion, datasets, sources, analytics, investigation, recovery, actions, jobs, llm, audit
  migrations/              Alembic migrations
  scripts/                 Sample dataset generator, OpenAPI export
  integrations/apps_script Google Sheets bridge script
  tests/                   pytest suites
infra/postgres/            Database role setup
docs/                      Master plan, architecture, data contracts, security model, test strategy, deployment guide
docker-compose.yml         Local PostgreSQL only
AGENTS.md                  Rules for coding agents
```

## Getting started

Prerequisites: Docker (or PostgreSQL 18), [uv](https://docs.astral.sh/uv/), Node.js 24 (see `apps/web/.nvmrc`).

Environment variable names (set values yourself; never commit them):

- Root `.env`: `POSTGRES_PASSWORD`, `ROOT_APP_DB_PASSWORD`, optional `POSTGRES_PORT`.
- `services/api/.env`: `ENVIRONMENT`, `DATABASE_URL`, `DATABASE_URL_MIGRATIONS`, `SESSION_SECRET`, `FRONTEND_ORIGIN`, `DEMO_OWNER_EMAIL`, `DEMO_OWNER_PASSWORD`, `DEMO_OWNER_NAME`, `DEMO_BUSINESS_NAME`, `DEMO_BUSINESS_CURRENCY`, `DEMO_BUSINESS_TIMEZONE`.
- Optional: `LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `GROQ_MODEL`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `SHEETS_BRIDGE_URL`, `SHEETS_BRIDGE_TOKEN`, `LOG_LEVEL`.
- Tests: `TEST_DATABASE_ADMIN_URL`, `TEST_APP_DB_PASSWORD`, `E2E_EMAIL`, `E2E_PASSWORD`.
- Frontend: see `apps/web/.env.example`.

Install and run:

```bash
# 1. Database
cp .env.example .env
docker compose up -d --wait

# 2. Backend
cd services/api
cp .env.example .env
uv sync --locked
uv run --env-file .env alembic upgrade head
uv run --env-file .env python -m app.cli seed-demo
uv run uvicorn app.main:create_app --factory --port 8000

# 3. Frontend (new terminal)
cd apps/web
cp .env.example .env.local
npm ci
npm run dev                  # http://localhost:3000
```

Sample data: generate four CSV files, then upload them from **Data > Upload files (CSV)**.

```bash
cd services/api
uv run python -m scripts.generate_dataset --seed 7 --out /tmp/biztective-demo
# options: --noise 0.02, --pii-column, --scenario return_spike, --scenario discount_spike
```

Tests:

```bash
cd services/api && uv run pytest
cd apps/web && npm run lint && npm run typecheck && npm test
cd apps/web && npx playwright test     # needs the backend running and E2E_EMAIL / E2E_PASSWORD
```

Demo login: the account created by `seed-demo` from the `DEMO_OWNER_EMAIL` and `DEMO_OWNER_PASSWORD` values in `services/api/.env`.

## Demo

TODO: add the demo video link and screenshots (see the VIDEO BRIEF below).

## Limitations / disclaimer

- Findings come from fixed rules over the uploaded data. Models, when enabled, only word the findings; they never change the numbers or run actions.
- Impacts of different incidents overlap and must not be added together.
- Some figures are unavailable when the data lacks what they need (for example product costs); the app says so on the page.
- Google Sheets export works only when the server has a service account or bridge configured.
- Dashboard layouts are stored in the browser, not on the server.
- The Biztective name and logo are recent; some backend text and stored investigation records may still say "ROOT". Internal identifiers keep the old name on purpose.
- Prototype built for a competition. TODO: add licence.

## Authors and acknowledgements

- Mahad Ghauri and Mahateer Muhammad (from the git history).
- Built for NaSCon Agentika 2.0.
- Built with Next.js, FastAPI, PostgreSQL, LangGraph, Base UI, shadcn/ui, motion, Recharts and lucide-react.

---

# VIDEO BRIEF

Target length: 150 to 180 seconds. Everything below was read from the code or seen in the running app on localhost, unless it says TODO.

## A. Basics

- **PROJECT NAME:** Biztective
- **TAGLINE:** Find out why your numbers changed.
- **WHAT IT DOES:** Biztective checks your sales data, finds the biggest changes between periods, and shows the records behind each one. It asks you the one question the data cannot answer, then updates its findings. You see what changed, why, and what it could not tell.
- **HOOK LINE:** **Your revenue dropped 7%, and nobody can tell you why.**
- **TWIST LINE:** Charts show that numbers moved. They do not show which product, which discount, or which record caused it.
- **APP URL + START COMMAND:** http://localhost:3000. Start the database with `docker compose up -d --wait`, the backend with `uv run uvicorn app.main:create_app --factory --port 8000` from `services/api`, and the web app with `npm run dev` from `apps/web`.
- **DEMO LOGIN / SEED DATA:** Run `uv run --env-file .env python -m app.cli seed-demo` and sign in with the `DEMO_OWNER_EMAIL` and `DEMO_OWNER_PASSWORD` from `services/api/.env`. Do not show the credentials on camera. The seeded business is named by `DEMO_BUSINESS_NAME`. Data comes from `python -m scripts.generate_dataset`.
- **LINK:** https://github.com/MahateerMuhammad/ROOT. TODO: demo URL.

## B. What exists

### Feature inventory

| Feature | Route or file | Priority |
|---|---|---|
| Animated sign-in with logo, orbiting icons, show/hide password, inline checks | `/login`, `components/ui/modern-animated-sign-in.tsx` | MUST SHOW |
| Business health dashboard of 12 draggable widgets | `/businesses/[id]`, `components/health/health-grid.tsx` | MUST SHOW |
| Incident list ("Biggest changes to look at") with severity, impact, link to evidence | dashboard widget, `datasets/[id]/analytics` | MUST SHOW |
| Analytics: period comparison table, incidents, "Show the evidence" checks, "What this can't show" | `datasets/[datasetId]/analytics` | MUST SHOW |
| Investigation workspace: question, findings, hypotheses, evidence, proof chain | `investigations/[investigationId]` | MUST SHOW |
| Answer a clarifying question (Yes / No / Not sure plus free text) | `components/investigation/question-form.tsx` | MUST SHOW |
| Weekly trend with metric switcher, hover tooltip, "Show as table" | dashboard widget | SHOULD SHOW |
| Customer segments (Loyal, At risk, Dormant, Too new to tell) | dashboard widget, `components/segments.tsx` | SHOULD SHOW |
| Data versions list and version detail with per-file rows, accepted, rejected, CSV downloads | `datasets`, `datasets/[datasetId]` | SHOULD SHOW |
| Upload four CSV files with time-zone field | `datasets/new` | SHOULD SHOW |
| Top bar: logo, business name, nav, theme toggle, user menu with sign out | `components/app-header.tsx` | SHOULD SHOW |
| Light and dark themes with animated toggle | `components/ui/sun-moon-theme-toggle.tsx` | MUST SHOW |
| Investigation replay (compare versions) | `investigations/[id]` (section "Investigation replay"), `components/investigation/version-diff.tsx` | SHOULD SHOW |
| Connect a PostgreSQL database (test, save, sync) | `datasets/connect`, `components/sources/connect-form.tsx` | SKIP-IF-SHORT |
| Decision center: recovery plans, strategy comparison, approve or reject | `decisions`, `recovery-plans/[planId]`, `components/recovery/*` | SKIP-IF-SHORT. TODO: no plan exists in the current local data, so it was only seen as an empty state |
| Evidence detail page | `evidence/[evidenceId]` | SKIP-IF-SHORT. TODO: not seen rendered |
| Integrations (Google Sheets) | `integrations`, `components/integrations/connect-sheet.tsx` | SKIP. Shows "Google Sheets is not configured on this server" locally |
| Data health card, Verified badge with info tip | dashboard widgets | SKIP-IF-SHORT |
| Mobile bottom navigation pill | `components/app-nav.tsx` | MUST SHOW (in the responsive scene) |

### Draggable widgets

- **Yes.** Library: a vendored component, `components/ui/draggable-widget-grid.tsx`, built on `motion` (`motion/react`). It is not an external drag library.
- **Widgets (12), in default order:** Net revenue (wide), Gross margin, Refund rate, Biggest changes to look at (large), Orders, Average order value, Active customers, Investigations, Weekly trend (large), Customer segments (large), Data freshness and quality (wide), Decisions (tall).
- **Default layout, desktop at 1440 px (3 columns):** Net revenue spans the top-left two columns and Gross margin sits top-right. Refund rate is below on the left, and the large "Biggest changes" tile fills the rest of that row. Orders, Average order value and Active customers follow in single tiles. Investigations, Weekly trend, Customer segments, Data health and Decisions come after.
- **Keyboard:** focus a widget, hold Alt, press the arrow keys. Touch: long press (350 ms) then drag. Mouse and pen: drag immediately.
- **Persistence:** yes, in the browser (`localStorage`, key `biztective:layout:<businessId>`), per business. Not stored on the server.
- **Reset:** the "Reset layout" button next to the Verified badge clears the saved layout and restores the default order.

### Navigation

- **Desktop (768 px and up):** a pill in the top bar with icon and label for Business health, Investigations, Decision center, Data and Integrations (Integrations only for owners and analysts).
- **Mobile (under 768 px):** a floating pill fixed to the bottom of the screen where only the current section shows its label, animated.
- **Active state:** from the URL, `aria-current="page"`, highlighted pill. Business health matches its exact path only.
- **Other patterns:** back links on detail pages ("← Data", "← Investigations"), user menu in the top bar (role, currency, time zone, switch business, Sign out). No sidebar, breadcrumbs or command palette.
- **Other breakpoint behaviour:** the user name in the top bar shows from 640 px up.

### Themes

- Light and dark both exist.
- Toggle: sun/moon button at the top right of the login page and in the top bar.
- First visit follows the system setting. After the toggle is used, the stored choice (`biztective-theme`) wins.
- The switch is animated: the sun and moon swap and the page colours fade over 900 ms. It is skipped under reduced motion.

### Animations

| Animation | File |
|---|---|
| Sun/moon swap and page colour fade | `components/ui/sun-moon-theme-toggle.tsx` |
| Expanding rings and orbiting icons on sign-in | `components/ui/modern-animated-sign-in.tsx`, `app/globals.css` (`ripple`, `orbit`) |
| Sign-in form reveals as it scrolls into view; input glow follows the pointer on hover | `components/ui/modern-animated-sign-in.tsx` |
| Spinner on the sign-in button while submitting | `components/ui/modern-animated-sign-in.tsx` |
| Widget drag and reflow, spring layout moves | `components/ui/draggable-widget-grid.tsx` |
| Bottom nav pill entrance spring and label width animation | `components/ui/bottom-nav-bar.tsx` |
| Weekly bars change height smoothly (500 ms) | `components/health/primitives.tsx` |
| Loading skeletons (pulse) | `app/(app)/businesses/[businessId]/loading.tsx`, `components/page.tsx` |
| Automatic page refresh while an investigation runs | `components/auto-refresh.tsx` |
| Hover and press states on nav items, rows and buttons | `components/ui/*`, `components/page.tsx` |

All animations respect `prefers-reduced-motion`. There are no toasts, modals, count-ups or page transitions in the code.

### Known visual bugs (fix or avoid on camera)

- Data page lists 9 versions, several with 2 order lines and two "Rejected". They are test uploads from development. Use a clean database or crop to Version 9.
- Two investigations are paused on "Needs your input" and one is completed. The dashboard shows an "Answer Biztective's question" button for them.
- Decisions shows "No recovery plans yet", so the plan and approval screens cannot be filmed until a plan is created. TODO.
- Integrations shows "Google Sheets is not configured on this server".
- On the sign-in page the orbiting icons pass behind the "Biztective" wordmark and its caption. One icon can clip at the left edge on narrower desktop widths.
- A small "N" button at the bottom left is the Next.js development badge. Film against a production build, or crop it.
- The sign-in button is dark grey in both themes by design.
- Existing investigation text may still contain "ROOT" until the data is regenerated.
- Not checked: the evidence page, recovery plan page, and a PostgreSQL sync against a real database.

## C. The story

### Three key moments

1. **The headline drop.** Dashboard: Net revenue $563,378, "Down 7.1% vs last period", Gross margin 47.1% (down 1.6 pts), Refund rate 3.9% (better). Under "Biggest changes to look at": High, "Discount rate in product 'P0023' jumped to 35.0% (usually 1.5%)", impact about $19.2K. A note says impacts overlap and must not be added together.
2. **The evidence.** Analytics page: the table of metrics now against before (Net revenue $563,377.62 against $606,414.49, discount rate 4.6% against 1.5%), then "Show the evidence (8 checks)" on P0023, with "Based on 135 order lines, 126 orders, 109 customers and 9 returns".
3. **The question.** Investigation: "Biztective needs your input", with "Did anything change for these products in the period, such as a new supplier, batch or specification?" and Yes, No, Not sure. After the answer, Findings move to a new version and the root cause reads "Heavier discounting or weaker price realisation is driving the change", with criteria "+12.2 pp" and "98.2%".

### Scene plan

| Time | Scene | On screen | Feature covered | Caption |
|---|---|---|---|---|
| 0:00-0:10 | Hook | Dark screen, then the dashboard's $563,378 and the red "Down 7.1%" chip | Net revenue tile | Revenue fell 7%. Why? |
| 0:10-0:25 | Overview | Sign-in with the logo and rings, sign in, land on Business health | Sign-in, dashboard | Meet Biztective |
| 0:25-0:40 | Biggest changes | Zoom on the incident list, hover the severity and impact, click the first row | Incident list | The biggest changes, ranked |
| 0:40-0:55 | Analytics and evidence | Period table, then "Show the evidence" on P0023 | Analytics page | Every number has proof |
| 0:55-1:10 | Data checks | Data versions, a version's file table with accepted and rejected rows | Datasets, version detail | Bad rows are caught |
| 1:10-1:25 | Upload and connect | Upload page with four files, then the PostgreSQL connection form | Upload, connect | Upload files or sync a database |
| 1:25-1:45 | Investigation | Open the investigation, read the question, choose Yes, send | Investigation workspace, question form | It asks what data cannot say |
| 1:45-1:55 | Replay and findings | New findings version, Investigation replay comparing versions | Findings, replay | Findings update, facts stay fixed |
| 1:55-2:15 | Draggable widgets | Drag Net revenue, move Orders with Alt+arrows, reload, press Reset layout | Widget grid | Arrange your dashboard |
| 2:15-2:35 | Navigation and responsive | Click through the top tabs, shrink the window to 390 px, bottom pill appears | Top nav, bottom nav | Built for phones too |
| 2:35-2:50 | Themes and animation | Click the sun/moon toggle, show the weekly bars and hover tooltip | Theme toggle, weekly trend | Light or dark |
| 2:50-3:00 | End card | Logo, name, tagline, link | Branding | Biztective. Find out why. |

The Decision center, evidence page and Integrations are not in the plan because the current local data has no recovery plan and Google Sheets is not configured. TODO: add a "plan and approve" scene after creating a recovery plan.

### Voiceover script (about 366 words, roughly 157 seconds at 140 words per minute)

1. 0:00: "Your revenue just dropped seven percent. (beat) The charts tell you it happened. They never tell you why. (beat) Most owners are left digging through spreadsheets, guessing."
2. 0:10: "Biztective answers that question. Sign in, and your business health sits on one page: revenue, margin, refunds, orders and customers, each one compared with the last period, in plain words, with a clear arrow showing whether it got better or worse."
3. 0:25: "Below the headline numbers, it ranks the biggest changes. Here, one product's discount jumped from one and a half percent to thirty-five, worth about nineteen thousand dollars. (beat) And it warns you that these impacts overlap, so they must never be added together."
4. 0:40: "Open the analytics and every claim comes with proof. The period comparison is exact to the cent, and Show the evidence lists each check, with the number of orders, customers and returns behind it."
5. 0:55: "Data is checked before anything is analysed. Every upload becomes its own version, and each rejected row is listed with the rule it broke, so nothing disappears quietly. You can download the issues as a file and fix them at the source."
6. 1:10: "Bring four CSV files, or connect a read-only PostgreSQL database. Either way, you get a checked copy and a fresh version, and your database password is never stored."
7. 1:25: "Then Biztective investigates. And where the data runs out, it asks you. Did anything change for these products, like a new supplier or batch? (beat) You answer in one click, and your words are kept exactly as written."
8. 1:45: "The findings move to a new version. Replay shows exactly what changed between the two, and proves the original measurements stayed the same."
9. 1:55: "The dashboard is yours. Drag any tile into place, or hold Alt and use the arrow keys. (beat) Your layout is remembered for next time, and one button puts everything back."
10. 2:15: "Move between sections from the top bar. Shrink the window, and it becomes a floating bar at the bottom, made for a phone in one hand."
11. 2:35: "Light or dark, in one tap. (beat) Even the weekly chart responds, showing each week's number as you point at it, with a table one click away."
12. 2:50: "Biztective. Find out why your numbers changed, and what to do next."

Start each line at the time shown. Keep the last line for the end card.

### Caption list

1. Revenue fell 7%. Why?
2. Meet Biztective
3. The biggest changes, ranked
4. Every number has proof
5. Bad rows are caught
6. Upload files or sync a database
7. It asks what data cannot say
8. Findings update, facts stay fixed
9. Arrange your dashboard
10. Built for phones too
11. Light or dark
12. Biztective. Find out why.

### 15-second cut

- 0:00-0:04, scene 1 (hook): $563,378, "Down 7.1%".
- 0:04-0:12, scene 3 (biggest changes): the P0023 discount jump of 1.5% to 35.0%, about $19.2K, with the line "It finds the cause, with proof."
- 0:12-0:15, scene 12 (end card): logo, name, tagline.
