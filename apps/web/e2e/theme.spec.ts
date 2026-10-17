import { expect, test } from "@playwright/test";

for (const setting of ["light", "dark", "legacy-dark", "system-dark", "blocked-storage"] as const) {
  test(`theme boot hydrates with an injected head script (${setting})`, async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error" && /hydrat|server rendered|script tag while rendering/i.test(message.text())) {
        errors.push(message.text());
      }
    });
    page.on("pageerror", (error) => errors.push(error.message));
    await page.emulateMedia({ colorScheme: setting === "legacy-dark" ? "light" : "dark" });
    await page.addInitScript((preference) => {
      if (preference === "blocked-storage") {
        Object.defineProperty(Storage.prototype, "getItem", {
          value() { throw new DOMException("Storage blocked", "SecurityError"); },
        });
        Object.defineProperty(Storage.prototype, "setItem", {
          value() { throw new DOMException("Storage blocked", "SecurityError"); },
        });
      } else if (preference === "legacy-dark") {
        localStorage.setItem("root-theme", "dark");
      } else if (preference === "light" || preference === "dark") {
        localStorage.setItem("biztective-theme", preference);
      }
      // An extension can insert an unrelated, non-async script before React hydrates.
      const observer = new MutationObserver(() => {
        if (!document.head) return;
        const script = document.createElement("script");
        script.type = "application/json"; // inert: simulate DOM interference without executing anything
        script.src = "chrome-extension://synthetic-test/content/popups-script.js";
        script.id = "extension-test-script";
        document.head.prepend(script);
        observer.disconnect();
      });
      observer.observe(document, { childList: true, subtree: true });
    }, setting);
    const response = await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
    // The submit button is enabled only after React hydrates.
    await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeEnabled();
    await expect(page.locator("#extension-test-script")).toHaveCount(1);
    const dark = setting !== "light" && setting !== "blocked-storage";
    await expect(page.locator("html")).toHaveClass(dark ? /\bdark\b/ : /^(?!.*\bdark\b)/);
    const nonce = response?.headers()["content-security-policy"]?.match(/'nonce-([^']+)'/)?.[1];
    expect(nonce).toBeTruthy();
    const themeScript = page.locator("script#biztective-theme-init");
    await expect(themeScript).toHaveCount(1);
    expect(await themeScript.evaluate((node) => node instanceof HTMLScriptElement ? node.nonce : null)).toBe(nonce);
    if (setting === "legacy-dark") {
      expect(await page.evaluate(() => localStorage.getItem("biztective-theme"))).toBe("dark");
    }
    if (setting === "blocked-storage") {
      await page.getByRole("button", { name: "Switch to dark theme" }).click();
      await expect(page.locator("html")).toHaveClass(/\bdark\b/);
    }
    expect(errors).toEqual([]);
  });
}
