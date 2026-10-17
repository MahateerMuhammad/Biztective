import { expect, test } from "@playwright/test";

const email = process.env.E2E_EMAIL ?? "";
const password = process.env.E2E_PASSWORD ?? "";

test.beforeAll(() => {
  expect(email, "E2E_EMAIL must be set").not.toBe("");
  expect(password, "E2E_PASSWORD must be set").not.toBe("");
});

test("unauthenticated visitors are sent to sign-in", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Sign in to ROOT" })).toBeVisible();

  await page.goto("/businesses/00000000-0000-0000-0000-000000000000");
  await expect(page).toHaveURL(/\/login$/);
});

test("pages are served with a nonce-based CSP", async ({ page }) => {
  const response = await page.goto("/login");
  const csp = response?.headers()["content-security-policy"] ?? "";
  expect(csp).toMatch(/script-src 'self' 'nonce-[A-Za-z0-9+/=]+' 'strict-dynamic'/);
  expect(csp).toContain("frame-ancestors 'none'");
  expect(response?.headers()["x-content-type-options"]).toBe("nosniff");
  expect(response?.headers()["x-powered-by"]).toBeUndefined();
});

test("wrong password shows an error without signing in", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(`${password}-wrong`);
  await page.getByRole("button", { name: "Sign in" }).click();
  // Next.js also renders an (empty) route-announcer alert, so match ours by its text.
  await expect(
    page.getByRole("alert").filter({ hasText: "Invalid email or password." }),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});

test("sign in, see the business shell, sign out", async ({ page, context }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/\/businesses\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByTestId("current-user")).not.toBeEmpty();
  await expect(page.getByRole("heading", { name: "Business health" })).toBeVisible();
  const sections = page.getByRole("navigation", { name: "Sections" });
  for (const name of ["Business health", "Investigations", "Decision center", "Data"]) {
    await expect(sections.getByRole("link", { name })).toBeVisible();
  }
  await expect(sections.getByRole("link", { name: "Business health" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByText(/^Owner · /)).toBeVisible();

  // The session cookie is HttpOnly: never readable from page scripts.
  const cookies = await context.cookies();
  const session = cookies.find((c) => c.name === "root_session");
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe("Lax");
  expect(await page.evaluate(() => document.cookie)).not.toContain("root_session");

  // A business the user does not belong to is indistinguishable from a missing one.
  await page.goto("/businesses/00000000-0000-0000-0000-000000000000");
  await expect(page.getByText("This page could not be found.")).toBeVisible();

  await page.goto("/");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);

  // No CSP violations or script errors; the 404 probe above is expected to log one error.
  expect(consoleErrors.filter((text) => !text.includes("404"))).toEqual([]);
});
