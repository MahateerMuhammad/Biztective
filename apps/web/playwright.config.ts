import { defineConfig, devices } from "@playwright/test";

const remoteUrl = process.env.PLAYWRIGHT_REMOTE_URL?.trim();
const backend = new URL(process.env.BACKEND_URL ?? "http://127.0.0.1:8000");

/**
 * Browser smoke tests against the real stack: Next.js (production build) + FastAPI + PostgreSQL.
 * Required env: BACKEND_URL (local) or PLAYWRIGHT_REMOTE_URL (deployed), E2E_EMAIL, E2E_PASSWORD
 * and, for local runs, the backend's own variables (DATABASE_URL, SESSION_SECRET,
 * FRONTEND_ORIGIN=http://localhost:3000, ENVIRONMENT).
 * The demo account must be seeded beforehand (`uv run python -m app.cli seed-demo`).
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: remoteUrl || "http://localhost:3000",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  ...(remoteUrl
    ? {}
    : {
        webServer: [
          {
            command: `uv run uvicorn app.main:create_app --factory --host ${backend.hostname} --port ${backend.port}`,
            cwd: "../../services/api",
            url: `${backend.origin}/api/v1/readyz`,
            reuseExistingServer: !process.env.CI,
            timeout: 60_000,
          },
          {
            command: "npm run start -- --hostname localhost --port 3000",
            url: "http://localhost:3000/login",
            reuseExistingServer: !process.env.CI,
            timeout: 60_000,
          },
        ],
      }),
});
