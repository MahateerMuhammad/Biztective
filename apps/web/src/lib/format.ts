/**
 * Display formatting only. Values arrive from the API as exact decimal strings; rounding here
 * is presentation (2 dp money, 1 dp percentages) and never feeds back into calculations.
 */

export function formatMoney(value: string | number | null | undefined, currency: string): string {
  if (value === null || value === undefined) return "—";
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: 2 }).format(number);
}

export function formatPercent(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${(number * 100).toFixed(1)}%`;
}

export function formatCount(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString("en-US");
}

const RATIOS = new Set([
  "gross_margin_pct",
  "discount_rate",
  "refund_rate",
  "unit_return_rate",
  "repeat_customer_rate",
  "matured_cohort_refund_rate",
]);
const COUNTS = new Set(["units_sold", "units_returned", "orders", "active_customers", "new_customers"]);

export function formatMetric(name: string, value: string | number | null | undefined, currency: string): string {
  if (RATIOS.has(name)) return formatPercent(value);
  if (COUNTS.has(name)) return formatCount(value);
  return formatMoney(value, currency);
}

/** Relative change as a signed percentage, or the absolute change in points for ratios. */
export function formatChange(
  name: string,
  change: { abs: string | number | null; rel: string | null } | undefined,
  currency: string,
): string {
  if (!change || change.abs === null) return "—";
  if (RATIOS.has(name)) {
    const points = Number(change.abs) * 100;
    return `${points >= 0 ? "+" : ""}${points.toFixed(1)} pts`;
  }
  if (change.rel === null) return formatMetric(name, change.abs, currency);
  const pct = Number(change.rel) * 100;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

export const METRIC_LABELS: Record<string, string> = {
  net_revenue: "Net revenue",
  net_sales: "Net sales",
  gross_sales: "Gross sales",
  discounts: "Discounts",
  refunds: "Refunds",
  gross_profit: "Gross profit",
  gross_margin_pct: "Gross margin",
  refund_rate: "Refund rate",
  discount_rate: "Discount rate",
  unit_return_rate: "Unit return rate",
  orders: "Orders",
  aov: "Average order value",
  active_customers: "Active customers",
  repeat_customer_rate: "Repeat-customer rate",
  matured_cohort_refund_rate: "Matured cohort refund rate",
};
