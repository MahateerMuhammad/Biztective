import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, test as base, type Page } from "@playwright/test";

export const email = process.env.E2E_EMAIL ?? "";
export const password = process.env.E2E_PASSWORD ?? "";
export const API = path.resolve(__dirname, "../../../services/api");

/** Every test fails on a browser console error or an uncaught page error. */
export const test = base.extend<{ consoleErrors: string[] }>({
  consoleErrors: [
    async ({ page }, use) => {
      const errors: string[] = [];
      page.on("console", (m) => {
        // A deliberately refused request (401/403/404/500 in failure-mode tests) is logged by
        // the browser as a resource error; those tests assert the UI instead.
        if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(m.text());
      });
      page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
      await use(errors);
      expect(errors, "browser console errors").toEqual([]);
    },
    { auto: true },
  ],
});
export { expect };

export async function signIn(page: Page, who = email, secret = password) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(who);
  await page.getByLabel("Password").fill(secret);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/businesses\/[0-9a-f-]{36}$/);
  return page.url().replace(/^https?:\/\/[^/]+/, "");
}

/** Realistic 26-week data from the seeded generator (dev tooling, never runtime code). */
export function generated(seed: number, scenario: string): Record<string, string> {
  const out = mkdtempSync(path.join(tmpdir(), "root-e2e-"));
  try {
    execFileSync(
      "uv",
      ["run", "--locked", "python", "-m", "scripts.generate_dataset", "--seed", String(seed), "--scenario", scenario, "--out", out],
      { cwd: API, stdio: "ignore" },
    );
    const files: Record<string, string> = {};
    for (const kind of ["orders", "products", "customers", "returns"]) {
      files[kind] = readFileSync(path.join(out, `${kind}.csv`), "utf8");
    }
    // An extra customer without orders makes each run's upload distinct (no duplicate refusal)
    // without changing any metric.
    files.customers = `${files.customers!.trimEnd()}\nE2E${Date.now().toString(36)},2025-01-04,east,social\n`;
    return files;
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

export async function upload(page: Page, files: Record<string, string>) {
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Data" }).click();
  await page.getByRole("link", { name: "Upload dataset" }).click();
  for (const [kind, body] of Object.entries(files)) {
    await page.getByLabel(`${kind[0]!.toUpperCase()}${kind.slice(1)} (.csv)`).setInputFiles({
      name: `${kind}.csv`,
      mimeType: "text/csv",
      buffer: Buffer.from(body),
    });
  }
  await page.getByRole("button", { name: "Upload and validate" }).click();
  await expect(page).toHaveURL(/\/datasets\/[0-9a-f-]{36}$/);
}

/** WCAG 2.0–2.2 A and AA rules; any violation fails with its rule IDs and targets. */
export async function expectAccessible(page: Page, label: string) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const found = result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  expect(found, `axe violations on ${label}`).toEqual([]);
  return result.passes.length;
}

/** The test stands in for the owner typing the code into their sheet (AMD-19 bridge). */
export async function writeVerificationCode(spreadsheetId: string, code: string) {
  const url = process.env.SHEETS_BRIDGE_URL ?? "";
  const token = process.env.SHEETS_BRIDGE_TOKEN ?? "";
  const call = async (op: string, args: unknown) => {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, op, spreadsheet_id: spreadsheetId, args }),
    });
    const body = (await response.json()) as { ok: boolean; result?: unknown; error?: string };
    expect(body.ok, `bridge ${op}: ${body.error ?? ""}`).toBe(true);
    return body.result;
  };
  const titles = (await call("titles", {})) as string[];
  if (!titles.includes("ROOT_VERIFY")) await call("add_tabs", { titles: ["ROOT_VERIFY"] });
  await call("put_rows", { data: [{ range: "'ROOT_VERIFY'!A1", values: [[code]] }] });
}

export const realSheets = Boolean(
  process.env.SHEETS_BRIDGE_URL && process.env.SHEETS_BRIDGE_TOKEN && process.env.ROOT_LIVE_SPREADSHEET_ID,
);
