import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

const email = process.env.E2E_EMAIL ?? "";
const password = process.env.E2E_PASSWORD ?? "";
const run = Date.now().toString(36);

/** Realistic 26-week data from the seeded generator (dev tooling, never runtime code). */
function generated(): Record<string, string> {
  const out = mkdtempSync(path.join(tmpdir(), "root-e2e-"));
  try {
    execFileSync(
      "uv",
      ["run", "--locked", "python", "-m", "scripts.generate_dataset", "--seed", "7", "--scenario", "discount_spike", "--out", out],
      { cwd: path.resolve(__dirname, "../../../services/api"), stdio: "ignore" },
    );
    const files: Record<string, string> = {};
    for (const kind of ["orders", "products", "customers", "returns"]) {
      files[kind] = readFileSync(path.join(out, `${kind}.csv`), "utf8");
    }
    // An extra customer without orders makes each run's upload distinct (no duplicate refusal)
    // without changing any metric.
    files.customers = `${files.customers!.trimEnd()}\nE2E${run},2025-01-04,east,social\n`;
    return files;
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/businesses\/[0-9a-f-]{36}$/);
}

test("a human answer revises the investigation while the measurements stay intact", async ({ page }) => {
  test.setTimeout(180_000);
  const files = generated();
  await signIn(page);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Data" }).click();
  await page.getByRole("link", { name: "Upload data" }).click();
  for (const [kind, body] of Object.entries(files)) {
    await page.getByLabel(`${kind[0]!.toUpperCase()}${kind.slice(1)} (.csv)`).setInputFiles({
      name: `${kind}.csv`,
      mimeType: "text/csv",
      buffer: Buffer.from(body),
    });
  }
  await page.getByRole("button", { name: "Upload and validate" }).click();
  await expect(page.getByRole("status")).toHaveText("Ready", { timeout: 60_000 });

  await page.getByRole("link", { name: "View analytics" }).click();
  await page.getByRole("button", { name: "Start investigation" }).click({ timeout: 60_000 });
  await expect(page).toHaveURL(/\/investigations\/[0-9a-f-]{36}$/);

  // The investigation pauses durably with one question; the page refreshes until then.
  await expect(page.getByRole("heading", { name: "Biztective needs your input" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("status").first()).toContainText("Waiting for your answer · version 1");
  await expect(page.getByText("Was the higher discounting in the period planned")).toBeVisible();
  const firstMeasurements = await page.getByRole("list", { name: "Measured facts" }).first().innerText();

  await page.getByLabel("Yes, it was a planned clearance or promotion campaign").check();
  await page.getByLabel(/Anything else Biztective should know/).fill("Spring clearance of last season's mats.");
  await page.getByRole("button", { name: "Send answer" }).click();

  await expect(page.getByRole("status").first()).toContainText("version 2", { timeout: 60_000 });
  await expect
    .poll(() => page.getByRole("list", { name: "Measured facts" }).first().innerText())
    .toBe(firstMeasurements);
  await expect(page.getByText("Deliberate business decision").first()).toBeVisible();
  await expect(page.getByText("“Spring clearance of last season's mats.”").first()).toBeVisible();
  await expect(page.getByText("added after your answer").first()).toBeVisible();

  const replay = page.getByRole("heading", { name: "Investigation replay" });
  await expect(replay).toBeVisible();
  await expect(page.getByText(/All original measurements are unchanged/)).toBeVisible();
  const changes = page.getByRole("list", { name: "Changes per incident" });
  await expect(changes).toContainText("Root cause changed: Heavier discounting → None");
  await expect(changes).toContainText("New alternative investigated: Prices relative to cost");
  await expect(changes).toContainText("Now recommended: Track the planned campaign");
});
