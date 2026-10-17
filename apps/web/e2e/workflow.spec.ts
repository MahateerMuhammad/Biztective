import type { Page } from "@playwright/test";

import {
  expect,
  expectAccessible,
  generated,
  realSheets,
  signIn,
  test,
  upload,
  writeVerificationCode,
} from "./support";

/**
 * Phase 9 completion gate, end to end on the real stack: a first-time owner uploads data,
 * understands the incident, takes part in the investigation and approves an action that is
 * then executed and verified in their own Google Sheet (when the AMD-19 bridge is configured).
 */
test.describe.configure({ mode: "serial" });

async function answerUntilDone(page: Page): Promise<number> {
  let answers = 0;
  for (let i = 0; i < 6; i++) {
    const status = page.getByRole("status").first();
    await expect(status).not.toContainText(/Queued|investigating/, { timeout: 90_000 });
    if (!(await page.getByRole("heading", { name: /Biztective needs your input/ }).isVisible())) break;
    if (answers === 0) {
      // Refresh while the workflow is suspended: the same question comes back, still answerable.
      const options = page.getByRole("radiogroup", { name: "Answer options" });
      const question = await options.textContent();
      await page.reload();
      await expect.poll(() => options.textContent()).toBe(question);
    }
    const before = await status.innerText();
    await page.getByLabel("Not sure").check();
    await page.getByRole("button", { name: "Send answer" }).dblclick(); // one answer, not two
    await expect(status).not.toHaveText(before, { timeout: 90_000 });
    answers += 1;
  }
  await expect(page.getByRole("status").first()).toContainText("Completed", { timeout: 90_000 });
  return answers;
}

test("a first-time owner goes from data to a verified action", async ({ page }) => {
  test.setTimeout(720_000);
  const home = await signIn(page);

  // 1. Data upload and quality report.
  await upload(page, generated(7, "discount_spike"));
  await expect(page.getByRole("status")).toHaveText("Ready", { timeout: 90_000 });
  await expectAccessible(page, "dataset quality report");

  // 2. Business health: metrics, prioritized incidents, freshness, customers.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Business health" }).click();
  const incidents = page.getByRole("list", { name: "Incidents by priority" });
  await expect(incidents).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText("Net revenue").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Data freshness and quality" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Customers by lifecycle segment" })).toBeVisible();
  await expect(page.getByRole("img", { name: /by week, from/ })).toBeVisible(); // chart summary
  await expectAccessible(page, "business health");

  // 3. Incident drill-down to the analytics evidence.
  await incidents.getByRole("link", { name: /Drill down/ }).first().click();
  await expect(page.getByRole("heading", { name: "Analytics" })).toBeVisible();
  await expect(page.locator("li[id^='incident-']").first()).toContainText("Evidence");
  await page.goBack();

  // 4. Start the investigation (a double click starts exactly one).
  await page.getByRole("button", { name: "Start investigation" }).dblclick();
  await expect(page).toHaveURL(/\/investigations\/[0-9a-f-]{36}$/);
  const investigationUrl = page.url();
  await expect(page.getByRole("heading", { name: "Agent progress" })).toBeVisible();
  const answers = await answerUntilDone(page);
  expect(answers).toBeGreaterThan(0);

  // 5. Proof chain with facts, hypotheses and source references.
  const chain = page.getByRole("list", { name: "Proof chain" }).first();
  await expect(chain).toBeVisible();
  await expect(chain.getByText("Fact").first()).toBeVisible();
  await expect(chain.getByText("Hypothesis").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Unresolved uncertainties" })).toBeVisible();
  await expectAccessible(page, "investigation workspace");
  await chain.getByRole("list", { name: "Evidence items" }).getByRole("link").first().click();
  await expect(page.getByText("Integrity verified")).toBeVisible();
  await expectAccessible(page, "evidence");
  await page.goBack();

  // 6. Replay: one version per answer, not one per click.
  await expect(page.getByRole("heading", { name: "Investigation replay" })).toBeVisible();
  await expect(page.getByRole("status").first()).toContainText(`version ${answers + 1}`);

  // 7. Decision center: a rejected plan has no effect, then a new plan is edited and approved.
  await page.getByRole("button", { name: "Plan a recovery" }).dblclick();
  await expect(page).toHaveURL(/\/recovery-plans\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("status").first()).toContainText("Waiting for your decision", { timeout: 90_000 });
  await expect(page.getByRole("table", { name: "Competing strategies compared" })).toBeVisible();
  await page.getByRole("button", { name: "Reject plan" }).click();
  await page.getByLabel("Why are you rejecting it? (optional)").fill("Trying a narrower plan.");
  await page.getByRole("button", { name: "Confirm rejection" }).click();
  await expect(page.getByRole("status").first()).toContainText("Rejected", { timeout: 90_000 });
  await expect(page.getByRole("heading", { name: "Execution" })).toHaveCount(0);

  await page.goto(investigationUrl);
  await page.getByRole("button", { name: "Plan a recovery" }).click();
  await expect(page.getByRole("status").first()).toContainText("Waiting for your decision", { timeout: 90_000 });
  await page.getByRole("button", { name: "Request changes" }).click();
  await page.getByLabel(/Budget \(/).fill("100000");
  for (const [scenario, value] of [["low", "1500"], ["mid", "2000"], ["high", "3000"]] as const) {
    await page.getByLabel(new RegExp(`cost of the review.*\\(${scenario}\\)`, "i")).first().fill(value);
  }
  await page.getByRole("button", { name: "Recalculate with these changes" }).click();
  await expect(page.getByRole("status").first()).toContainText("version 2", { timeout: 90_000 });
  await expectAccessible(page, "decision center");
  await page.getByRole("button", { name: "Review approval" }).click();
  await page.getByRole("button", { name: "Confirm approval" }).dblclick(); // one approval
  await expect(page.getByRole("status").first()).toContainText("Approved", { timeout: 90_000 });
  const history = page.getByRole("list", { name: "Decisions" });
  await expect(history.getByText(/approved version 2/)).toHaveCount(1);

  // 8. Execution in the owner's real Google Sheet, verified by read-back.
  if (!realSheets) {
    await expect(page.getByText("Connect a Google Sheet first.")).toBeVisible();
    test.info().annotations.push({ type: "not run", description: "real Sheets execution: no bridge configured" });
    return;
  }
  const plan = page.url();
  await page.goto(`${home}/integrations`);
  await expect(page.getByRole("heading", { name: "Google Sheets" })).toBeVisible(); // streamed in
  if (await page.getByRole("button", { name: "Connect", exact: true }).isVisible()) {
    const sid = process.env.ROOT_LIVE_SPREADSHEET_ID!;
    await page.getByLabel("Google Sheets link or spreadsheet ID").fill(sid);
    await page.getByRole("button", { name: "Connect", exact: true }).click();
    const code = (await page.locator("strong.font-mono").innerText()).trim();
    await writeVerificationCode(sid, code);
    await page.getByRole("button", { name: "Check and verify" }).click();
  }
  await expect(page.getByText(/Connected and verified/).first()).toBeVisible({ timeout: 60_000 });
  await expectAccessible(page, "integrations");
  await page.goto(plan);
  await page.getByRole("button", { name: "Execute in Google Sheets" }).dblclick(); // one action
  await expect(page.getByText(/Done and verified/)).toBeVisible({ timeout: 360_000 });
  await expect(page.getByRole("link", { name: "Open the spreadsheet" })).toHaveAttribute(
    "href",
    `https://docs.google.com/spreadsheets/d/${process.env.ROOT_LIVE_SPREADSHEET_ID}`,
  );
  await expect(page.getByRole("heading", { name: /→ Google Sheets/ })).toHaveCount(1);
});
