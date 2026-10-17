/**
 * Display formatting only. Values arrive from the API as exact decimal strings; rounding here
 * is presentation (2 dp money, 1 dp percentages) and never feeds back into calculations.
 */

export function formatMoney(value: string | number | null | undefined, currency: string, digits = 2): string {
  if (value === null || value === undefined) return "—";
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(number);
}

/** Money for big tiles: no cents, with thousands separators ($563,378). */
export function formatMoneyWhole(value: string | number | null | undefined, currency: string): string {
  return formatMoney(value, currency, 0);
}

/** Money in compact units ($19.2K); the exact value goes in a tooltip. */
export function formatMoneyCompact(value: string | number | null | undefined, currency: string): string {
  if (value === null || value === undefined) return "—";
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency, notation: "compact", maximumFractionDigits: 1 }).format(number);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-10-16" (or a full ISO timestamp) as "Oct 16"; with `year`, "Oct 16, 2026". Pure string work, so server and browser agree. */
export function formatDate(iso: string | null | undefined, year = false): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? "");
  if (!m) return "—";
  const month = MONTHS[Number(m[2]) - 1];
  if (!month) return "—";
  return `${month} ${Number(m[3])}${year ? `, ${m[1]}` : ""}`;
}

/** "Oct 16, 9:18 PM" (UTC, like the stored timestamps). */
export function formatDateTime(iso: string | null | undefined): string {
  const m = /^\d{4}-\d{2}-\d{2}T(\d{2}):(\d{2})/.exec(iso ?? "");
  if (!m) return formatDate(iso);
  const hour = Number(m[1]);
  return `${formatDate(iso)}, ${hour % 12 === 0 ? 12 : hour % 12}:${m[2]} ${hour < 12 ? "AM" : "PM"}`;
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

/** Short names of the hypothesis catalog templates (services/api app/investigation/catalog.py). */
export const TEMPLATE_LABELS: Record<string, string> = {
  H_RETURNS_QUALITY: "Product quality driving returns",
  H_FULFILLMENT: "Delivery problems",
  H_PRICE_DISCOUNT: "Heavier discounting",
  H_SEGMENT_SHIFT: "Regular customers buying less",
  H_CONCENTRATION: "A few customers",
  H_CHANNEL: "One sales channel",
  H_DATA_QUALITY: "A data artefact",
  H_EXTERNAL_CONTEXT: "An event outside the data",
  H_COST_PRICE: "Prices relative to cost",
};

export const STATUS_LABELS: Record<string, string> = {
  pending: "Pending",
  supported: "Supported",
  refuted: "Ruled out",
  inconclusive: "Inconclusive",
  needs_human_context: "Needs your context",
};

export function templateLabel(template: string | null | undefined): string {
  if (!template) return "None";
  return TEMPLATE_LABELS[template] ?? template;
}

/** Plan validation codes in words (AMD-17). */
export const PLAN_ISSUES: Record<string, string> = {
  no_strategy_selected: "Select at least one strategy.",
  unknown_strategy: "A selected strategy is not part of this plan.",
  strategy_ineligible: "A selected strategy is not supported by the evidence (see its eligibility).",
  action_type_not_allowed: "A selected strategy needs an action type you have not permitted.",
  missing_assumption: "A selected strategy needs a cost estimate from you.",
  budget_missing: "Set a budget: the selected strategies have a cost.",
  budget_exceeded: "The selected strategies can cost more than the budget (high scenario).",
  discount_exceeds_max: "A discount is above the maximum you allowed.",
  invalid_customer_targeting: "Some targeted customers are not eligible or are excluded.",
  no_targets: "A customer strategy has no customers selected.",
  overlapping_scope: "Several selected strategies address the same problem; their effects may overlap.",
  non_positive_contribution_margin: "The affected sales earn no contribution; see the strategy's limits.",
};

export const INELIGIBLE: Record<string, string> = {
  missing_cost_data: "product costs are missing, so its cost cannot be computed",
  no_excess_discount: "there is no excess discount to remove",
  no_customer_activity: "no customer activity was found in the affected slice",
};

export const BREAK_EVEN: Record<string, string> = {
  above_one: "does not pay back even at full success",
  offer_exceeds_contribution: "never pays back: the offer costs more than an order contributes",
  no_recoverable_amount: "nothing measurable to recover",
  not_estimated: "not estimated",
};
