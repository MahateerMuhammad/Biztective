import { expect, test, type Page } from "@playwright/test";

const email = process.env.E2E_EMAIL ?? "";
const password = process.env.E2E_PASSWORD ?? "";

// Unique IDs per run: identical re-uploads are (correctly) refused as duplicates.
const run = Date.now().toString(36);

function files(ordersHeader: string): Record<string, string> {
  return {
    products: "product_id,product_name,category,unit_cost,list_price\nP1,=SUM(A1),kitchen,2.00,5.00\nP2,Mat,fitness,4.00,9.00\n",
    customers: "customer_id,region,email\nC1,north,someone@example.test\nC2,south,other@example.test\n",
    orders:
      `${ordersHeader}\n` +
      `L${run}a,O${run}a,2026-03-02T10:00:00,C1,P1,2,5.00\n` +
      `L${run}b,O${run}b,2026-03-03,C2,P2,1,9.00\n` +
      `L${run}c,O${run}c,03/04/2026,C2,P2,1,9.00\n`,
    returns: "return_id,order_line_id,return_date,quantity_returned,refund_amount,reason_code\n",
  };
}

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/businesses\/[0-9a-f-]{36}$/);
}

async function uploadFiles(page: Page, content: Record<string, string>) {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Data" }).click();
  await page.getByRole("link", { name: "Upload data" }).click();
  await expect(page.getByRole("heading", { name: "Upload data" })).toBeVisible();
  for (const [kind, body] of Object.entries(content)) {
    const label = `${kind[0]!.toUpperCase()}${kind.slice(1)} (.csv)`;
    await page.getByLabel(label).setInputFiles({
      name: `${kind}.csv`,
      mimeType: "text/csv",
      buffer: Buffer.from(body),
    });
  }
  await page.getByRole("button", { name: "Upload and validate" }).click();
  await expect(page).toHaveURL(/\/datasets\/[0-9a-f-]{36}$/);
}

test("upload, validate and review data quality", async ({ page }) => {
  await signIn(page);
  await uploadFiles(
    page,
    files("order_line_id,order_id,order_date,customer_id,product_id,quantity,unit_price"),
  );

  // The page refreshes itself until validation finishes (worker runs in the API process).
  await expect(page.getByRole("status")).toHaveText(/Rejected|Ready/, { timeout: 30_000 });
  // 1 of 3 order lines has an ambiguous date → above the 5% threshold → rejected, nothing loaded.
  await expect(page.getByRole("status")).toHaveText("Rejected: data quality problems");
  const report = page.getByRole("table", { name: "Issue counts by file and rule" });
  await expect(report.getByRole("row", { name: /orders V-013 error/ })).toBeVisible();
  await expect(report.getByRole("row", { name: /customers V-041 warning/ })).toBeVisible();
  await expect(page.getByRole("cell", { name: "03/04/2026" })).toBeVisible();

  const download = await page.request.get(
    (await page.getByRole("link", { name: "Download all issues (CSV)" }).getAttribute("href"))!,
  );
  expect(download.status()).toBe(200);
  expect(download.headers()["content-type"]).toContain("text/csv");
  expect(await download.text()).toContain("V-013");
});

test("uncertain column matches wait for human confirmation", async ({ page }) => {
  await signIn(page);
  const content = files("order_line_id,order_id,order_date,customer_id,product_id,qty,unit_price");
  content.orders = content.orders!.replace(/\n[^\n]*03\/04\/2026[^\n]*/, "");
  await uploadFiles(page, content);

  await expect(page.getByRole("status")).toHaveText("Waiting for column mapping confirmation");
  const select = page.getByLabel(/quantity/);
  await expect(select).toHaveValue("qty");
  await page.getByRole("button", { name: "Confirm mapping and validate" }).click();
  await expect(page.getByRole("status")).toHaveText("Ready", { timeout: 30_000 });

  const exportHref = await page.getByRole("link", { name: "CSV of products" }).getAttribute("href");
  const exported = await (await page.request.get(exportHref!)).text();
  expect(exported).toContain("'=SUM(A1)"); // formula neutralised for spreadsheets
});

test("analytics page shows computed metrics and data limitations", async ({ page }) => {
  await signIn(page);
  const content = files("order_line_id,order_id,order_date,customer_id,product_id,quantity,unit_price");
  content.orders = content.orders!.replace(/\n[^\n]*03\/04\/2026[^\n]*/, "");
  content.products = content.products!.replace("2.00,5.00", ",5.00"); // P1 without unit cost
  await uploadFiles(page, content); // exact headers: no mapping decision needed
  await expect(page.getByRole("status")).toHaveText("Ready", { timeout: 30_000 });

  await page.getByRole("link", { name: "View analytics" }).click();
  await expect(page.getByRole("heading", { name: "Analytics" })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("not enough history", { timeout: 30_000 });
  const limits = page.getByRole("alert").filter({ hasText: "Data limitations" });
  await expect(limits).toContainText("missing_costs");
  await expect(limits).toContainText("insufficient_history");
  const products = page.getByRole("table", { name: "Product performance and return rates" });
  await expect(products.getByRole("row", { name: /P2/ })).toContainText("$9.00");
  const segments = page.getByRole("table", { name: "Customers by lifecycle segment" });
  await expect(segments.getByRole("row", { name: /Insufficient history/ })).toContainText("2");
});
