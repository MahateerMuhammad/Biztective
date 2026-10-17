import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import type { Page } from "@playwright/test";

import { API, email, expect, expectAccessible, password, signIn, test } from "./support";

/**
 * Phase 9 interface qualities on the real stack: keyboard, mobile, failure modes, tenancy,
 * empty states, slow networks, route performance and accessibility.
 */

const SECTIONS = ["", "/investigations", "/decisions", "/datasets", "/integrations"];

async function firstLink(page: Page, path: string): Promise<string | null> {
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const link = page.getByRole("table").getByRole("link").first();
  return (await link.count()) ? link.getAttribute("href") : null;
}

test("everything is reachable with the keyboard alone", async ({ page }) => {
  await page.goto("/login");
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Email")).toBeFocused();
  await page.keyboard.type(email);
  await page.keyboard.press("Tab");
  await page.keyboard.type(password);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/businesses\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { level: 1, name: "Business health" })).toBeVisible();

  // The first stop is the skip link; it moves focus past the header to the content.
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible(); // visible when focused
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeFocused();

  // Section navigation by keyboard: Tab to "Investigations" and follow it with Enter.
  const target = page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Investigations" });
  for (let i = 0; i < 25 && !(await target.evaluate((el) => el === document.activeElement)); i++) {
    await page.keyboard.press("Tab");
  }
  await expect(target).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { level: 1, name: "Investigations" })).toBeVisible();
  await expect(target).toHaveAttribute("aria-current", "page");
});

test("main screens fit a phone screen without sideways scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const home = await signIn(page);
  const workspace = await firstLink(page, `${home}/investigations`);
  const routes = [...SECTIONS.map((s) => `${home}${s}`), ...(workspace ? [workspace] : [])];
  for (const route of routes) {
    await page.goto(route);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `horizontal overflow on ${route}`).toBeLessThanOrEqual(1);
  }
  await page.goto(home);
  await expect(page.getByRole("heading", { level: 1, name: "Business health" })).toBeVisible();
  await expectAccessible(page, "business health (mobile)");
});

test("an expired session leads back to sign-in and never shows success", async ({ page, context }) => {
  const home = await signIn(page);
  await page.goto(`${home}/datasets/new`);
  await expect(page.getByRole("heading", { name: "Upload dataset" })).toBeVisible();
  await context.clearCookies(); // the session expires while the form is open
  for (const kind of ["orders", "products", "customers", "returns"]) {
    await page.getByLabel(`${kind[0]!.toUpperCase()}${kind.slice(1)} (.csv)`).setInputFiles({
      name: `${kind}.csv`,
      mimeType: "text/csv",
      buffer: Buffer.from("a,b\n1,2\n"),
    });
  }
  await page.getByRole("button", { name: "Upload and validate" }).click();
  await expect(page.getByRole("alert").filter({ hasText: /./ }).last()).toBeVisible();
  await expect(page).toHaveURL(/\/datasets\/new$/);
  // Any server-rendered page now redirects to sign-in.
  await page.goto(`${home}/decisions`);
  await expect(page).toHaveURL(/\/login$/);
});

test("server errors are reported and nothing is shown as done", async ({ page }) => {
  const home = await signIn(page);
  await page.route("**/api/v1/businesses/*/datasets", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await route.fulfill({
      status: 503,
      contentType: "application/problem+json",
      body: JSON.stringify({
        type: "about:blank",
        title: "Service Unavailable",
        status: 503,
        detail: "The service is temporarily unavailable.",
        code: "unavailable",
        request_id: null,
      }),
    });
  });
  await page.goto(`${home}/datasets/new`);
  for (const kind of ["orders", "products", "customers", "returns"]) {
    await page.getByLabel(`${kind[0]!.toUpperCase()}${kind.slice(1)} (.csv)`).setInputFiles({
      name: `${kind}.csv`,
      mimeType: "text/csv",
      buffer: Buffer.from("a,b\n1,2\n"),
    });
  }
  await page.getByRole("button", { name: "Upload and validate" }).click();
  await expect(page.getByText("The service is temporarily unavailable.")).toBeVisible();
  await expect(page).toHaveURL(/\/datasets\/new$/);

  // A missing resource is a plain not-found page, not an error or someone else's data. (It
  // streams behind the loading skeleton, so the HTTP status is already 200 by then; a
  // foreign business is refused earlier, with a real 404, in the tenancy test below.)
  await page.goto(`${home}/investigations/00000000-0000-7000-8000-000000000000`);
  await expect(page.getByText("This page could not be found.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Investigation workspace" })).toHaveCount(0);
});

test("another business's pages are not found, and a new business sees empty states", async ({ page }) => {
  const home = await signIn(page);
  const workspace = await firstLink(page, `${home}/investigations`);
  const businessName = (await page.getByRole("banner").getByRole("navigation").innerText()).trim();

  // A second, unrelated tenant (seeded like the demo owner; dev tooling only).
  const otherEmail = `other.${Date.now().toString(36)}@example.test`;
  const otherPassword = randomBytes(18).toString("hex");
  execFileSync("uv", ["run", "--locked", "python", "-m", "app.cli", "seed-demo"], {
    cwd: API,
    stdio: "ignore",
    env: { ...process.env, DEMO_OWNER_EMAIL: otherEmail, DEMO_OWNER_PASSWORD: otherPassword, DEMO_BUSINESS_NAME: "Other Shop" },
  });
  await page.context().clearCookies();
  const theirs = await signIn(page, otherEmail, otherPassword);
  expect(theirs).not.toBe(home);

  for (const url of [home, `${home}/decisions`, ...(workspace ? [workspace] : [])]) {
    const response = await page.goto(url);
    expect(response?.status(), url).toBe(404);
    await expect(page.getByText(businessName)).toHaveCount(0);
  }

  // Empty states guide a first-time business to its first step.
  await page.goto(theirs);
  await expect(page.getByRole("heading", { name: "Start with your sales data" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Upload dataset" })).toBeVisible();
  await page.goto(`${theirs}/investigations`);
  await expect(page.getByText("No investigations yet.")).toBeVisible();
  await page.goto(`${theirs}/decisions`);
  await expect(page.getByText(/No recovery plans yet/)).toBeVisible();
  await page.goto(`${theirs}/datasets`);
  await expect(page.getByText("No datasets yet.")).toBeVisible();
  await expectAccessible(page, "empty data page");
});

test("on a slow network the screen shows it is loading, then the content", async ({ page }) => {
  const home = await signIn(page);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.emulateNetworkConditions", {
    offline: false,
    latency: 1500,
    downloadThroughput: (400 * 1024) / 8,
    uploadThroughput: (400 * 1024) / 8,
  });
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Decision center" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Loading…" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Decision center" })).toBeVisible({ timeout: 30_000 });
  await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await page.goto(home);
});

test("main routes load fast and ship little JavaScript", async ({ page }) => {
  const home = await signIn(page);
  const workspace = await firstLink(page, `${home}/investigations`);
  const plan = await firstLink(page, `${home}/decisions`);
  const routes: [string, string][] = [
    ["Business health", home],
    ["Investigations", `${home}/investigations`],
    ["Decision center", `${home}/decisions`],
    ["Data", `${home}/datasets`],
    ...(workspace ? ([["Investigation workspace", workspace]] as [string, string][]) : []),
    ...(plan ? ([["Recovery plan", plan]] as [string, string][]) : []),
  ];
  const results = [];
  for (const [name, url] of routes) {
    await page.goto("about:blank");
    const started = Date.now();
    await page.goto(url, { waitUntil: "load" });
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const visible = Date.now() - started;
    const timing = await page.evaluate(() => {
      const nav = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming;
      const scripts = (performance.getEntriesByType("resource") as PerformanceResourceTiming[]).filter(
        (r) => r.initiatorType === "script" || r.name.endsWith(".js"),
      );
      return {
        ttfb: Math.round(nav.responseStart),
        domContentLoaded: Math.round(nav.domContentLoadedEventEnd),
        load: Math.round(nav.loadEventEnd),
        jsTransferredKb: Math.round(scripts.reduce((n, r) => n + r.encodedBodySize, 0) / 1024),
        jsDecodedKb: Math.round(scripts.reduce((n, r) => n + r.decodedBodySize, 0) / 1024),
        scripts: scripts.length,
      };
    });
    results.push({ route: name, headingVisibleMs: visible, ...timing });
  }
  const out = path.resolve(__dirname, "../test-results");
  mkdirSync(out, { recursive: true });
  writeFileSync(path.join(out, "route-performance.json"), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results));
  for (const r of results) {
    // Budgets on a production build (Phase 9): headroom over what was measured locally.
    expect(r.jsTransferredKb, `${r.route} JavaScript`).toBeLessThanOrEqual(400);
    expect(r.headingVisibleMs, `${r.route} time to content`).toBeLessThanOrEqual(5000);
  }
});

test("secondary screens meet WCAG 2.2 AA (axe)", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in to ROOT" })).toBeVisible();
  await expectAccessible(page, "sign in");
  const home = await signIn(page);
  for (const section of SECTIONS.slice(1)) {
    await page.goto(`${home}${section}`);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expectAccessible(page, section);
  }
});
