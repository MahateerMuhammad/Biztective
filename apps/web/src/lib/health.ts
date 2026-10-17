/**
 * Business health, prepared for display. Everything here is derived from the API responses the
 * page already fetches; nothing is measured or estimated again. The result is plain data (strings,
 * numbers, booleans) so it can cross from the server page to the client widget grid.
 */

import type { ComparisonRow, IncidentSummary, MetricValues, Restriction } from "@/lib/api/types";
import {
  METRIC_LABELS,
  formatChange,
  formatCount,
  formatDate,
  formatDateTime,
  formatMetric,
  formatMoneyCompact,
  formatMoneyWhole,
  formatPercent,
} from "@/lib/format";

/** Headline metrics, and whether a rise is good news for the business. */
export const KPIS: { key: string; up: "good" | "bad" }[] = [
  { key: "net_revenue", up: "good" },
  { key: "gross_margin_pct", up: "good" },
  { key: "refund_rate", up: "bad" },
  { key: "orders", up: "good" },
  { key: "aov", up: "good" },
  { key: "active_customers", up: "good" },
];
export const TREND = ["net_revenue", "refund_rate", "discount_rate", "orders", "active_customers"];

export const SEGMENT_LABELS: Record<string, string> = {
  loyal: "Loyal",
  at_risk: "At risk",
  dormant: "Dormant",
  insufficient_history: "Too new to tell",
};

export type Outcome = "better" | "worse" | "flat";

export interface KpiView {
  label: string;
  value: string;
  /** The same value for big tiles: money without cents. Equals `value` for counts and ratios. */
  short: string;
  /** The change in words and signs ("+3.2%", "-0.4 pts"), or "—" when there is none. */
  change: string;
  arrow: "up" | "down" | "flat";
  /** Whether the change is good or bad news, which depends on the metric. */
  outcome: Outcome;
}

export interface IncidentView {
  id: string;
  rank: number;
  severity: IncidentSummary["severity"];
  /** One plain sentence: "Discount rate in product “P0023” jumped to 35.0% (usually 1.5%)". */
  sentence: string;
  /** Severity as a word ("High"). */
  severityLabel: string;
  /** Estimated impact in compact units ("$19.2K") and exactly ("$19,213.40"). */
  impact: string;
  impactExact: string;
  href: string;
}

export interface TrendSeries {
  key: string;
  label: string;
  /** Numeric values for the bars; a missing week is null. */
  values: (number | null)[];
  /** The same values as display text, for the table and tooltips (exact). */
  text: string[];
  /** For the big number: money without cents. */
  short: string[];
}

export interface HealthData {
  businessId: string;
  datasetId: string;
  datasetVersion: number;
  canAct: boolean;
  analysing: boolean;
  analysisStatus: string | null;
  kpis: Record<string, KpiView> | null;
  /** The previous period's value of each headline metric, as display text (only when the API gives one). */
  previous: Record<string, string>;
  incidents: IncidentView[];
  /** How many incidents the analysis found in all (the list above is capped). */
  incidentsTotal: number;
  incidentsHref: string;
  /** "Jul 5, 2026": the last day the data covers. */
  through: string;
  /** The last (up to 26) weeks, whole business: one series per headline and trend metric. */
  weekly: { weeks: string[]; series: Record<string, TrendSeries> } | null;
  segments: {
    customers: string;
    repeatRate: string | null;
    rows: {
      key: string;
      label: string;
      /** Share of customers, 0 to 1. */
      portion: number;
      /** Share relative to the largest segment, 0 to 1, for the row bars. */
      share: number;
      shareText: string;
      count: string;
      revenue: string;
    }[];
  } | null;
  quality: {
    covers: string;
    uploaded: string;
    uploadedExact: string;
    orderLines: string;
    status: string;
    /** What is wrong, when something is. */
    detail: string | null;
    clean: boolean;
    restrictions: string[];
    reportHref: string;
  };
  investigations: { id: string; date: string; status: string; statusKey: string; href: string }[];
  /** The primary action for Biztective's investigation, when the page offers one. */
  investigationAction: { kind: "link"; href: string; label: string } | { kind: "start" } | null;
  plans: { id: string; text: string; href: string }[];
  decisionsHref: string;
  investigationsHref: string;
}

const num = (v: string | number | null | undefined): number | null => {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const MONEY = new Set(["net_revenue", "aov", "gross_profit", "net_sales", "gross_sales", "discounts", "refunds"]);
const money = (metric: string) => MONEY.has(metric);

export function kpiView(row: ComparisonRow, metric: string, up: "good" | "bad", currency: string): KpiView {
  const change = row.change?.[metric];
  const delta = change?.abs === null || change?.abs === undefined ? null : Number(change.abs);
  const outcome: Outcome = delta === null || delta === 0 ? "flat" : (delta > 0) === (up === "good") ? "better" : "worse";
  return {
    label: METRIC_LABELS[metric] ?? metric,
    value: formatMetric(metric, row.current[metric], currency),
    short: money(metric) ? formatMoneyWhole(row.current[metric], currency) : formatMetric(metric, row.current[metric], currency),
    change: formatChange(metric, change, currency),
    arrow: delta === null || delta === 0 ? "flat" : delta > 0 ? "up" : "down",
    outcome,
  };
}

const SEVERITY_LABELS: Record<IncidentSummary["severity"], string> = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };

/** "Discount rate in product “P0023” jumped to 35.0% (usually 1.5%)": what changed, in one sentence. */
export function incidentSentence(i: IncidentSummary, currency: string): string {
  const metric = METRIC_LABELS[i.metric] ?? i.metric;
  const where = i.dimension === "overall" ? `${metric} across the business` : `${metric} in ${i.dimension} “${i.value}”`;
  const now = num(i.current_value);
  const usual = num(i.baseline_median);
  let verb = "changed to";
  if (now !== null && usual !== null) {
    const ratio = usual !== 0 ? now / usual : Infinity;
    verb = now >= usual ? (ratio >= 2 ? "jumped to" : "rose to") : ratio <= 0.5 ? "dropped to" : "fell to";
  }
  return `${where} ${verb} ${formatMetric(i.metric, i.current_value, currency)} (usually ${formatMetric(i.metric, i.baseline_median, currency)})`;
}

const INVESTIGATION_STATUS: Record<string, string> = {
  awaiting_human: "Needs your input",
  completed: "Completed",
  failed: "Couldn’t finish",
  running: "In progress",
  queued: "Waiting to start",
};

export const investigationStatus = (status: string): string =>
  INVESTIGATION_STATUS[status] ?? status.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());

export interface HealthInput {
  businessId: string;
  canAct: boolean;
  currency: string;
  dataset: {
    id: string;
    version: number;
    date_min: string | null;
    date_max: string | null;
    created_at: string;
    orders_rows: number | null;
  };
  issues: { severity: string; count: number }[];
  analysis: { status: string; incidents: IncidentSummary[]; restrictions: Restriction[] } | null;
  overall: ComparisonRow | undefined;
  comparisonRestrictions: Restriction[];
  weekly: { week: string; values: MetricValues }[];
  segments: { customers: number; segments: { segment: string; customers: number; share: string | null; monetary_365: string }[] } | null;
  investigations: { id: string; dataset_id: string; status: string; created_at: string }[];
  plans: { id: string; status: string; execution_status?: string | null; created_at: string }[];
}

export function buildHealth(input: HealthInput): HealthData {
  const { businessId, currency, dataset, analysis, overall } = input;
  const base = `/businesses/${businessId}`;
  const analysing = !analysis || analysis.status === "queued" || analysis.status === "running";
  const points = input.weekly.slice(-26);

  const shown = [...(analysis?.incidents ?? [])].sort((a, b) => a.rank - b.rank).slice(0, 3);
  const incidents = shown.map((i) => ({
      id: i.id,
      rank: i.rank,
      severity: i.severity,
      severityLabel: SEVERITY_LABELS[i.severity],
      sentence: incidentSentence(i, currency),
      impact: formatMoneyCompact(i.impact, currency),
      impactExact: formatMetric("net_revenue", i.impact, currency),
      href: `${base}/datasets/${dataset.id}/analytics#incident-${i.id}`,
    }));

  const errors = input.issues.filter((i) => i.severity === "error").reduce((n, i) => n + i.count, 0);
  const warnings = input.issues.filter((i) => i.severity !== "error").reduce((n, i) => n + i.count, 0);
  const clean = errors === 0 && warnings === 0;
  const restrictions = [...(analysis?.restrictions ?? []), ...input.comparisonRestrictions]
    .filter((r, i, all) => all.findIndex((x) => x.code === r.code) === i)
    .slice(0, 4)
    .map((r) => r.message);

  const datasetInvestigations = input.investigations.filter((i) => i.dataset_id === dataset.id);
  const open = datasetInvestigations[0];
  const action: HealthData["investigationAction"] =
    incidents.length === 0
      ? null
      : open
        ? {
            kind: "link",
            href: `${base}/investigations/${open.id}`,
            label: open.status === "awaiting_human" ? "Answer Biztective's question" : "Open the investigation",
          }
        : input.canAct
          ? { kind: "start" }
          : null;

  const segments = input.segments;
  const largest = Math.max(0, ...(segments?.segments ?? []).map((s) => num(s.share) ?? 0));

  return {
    businessId,
    datasetId: dataset.id,
    datasetVersion: dataset.version,
    canAct: input.canAct,
    analysing,
    analysisStatus: analysis?.status ?? null,
    kpis: overall ? Object.fromEntries(KPIS.map((k) => [k.key, kpiView(overall, k.key, k.up, currency)])) : null,
    previous: overall?.previous
      ? Object.fromEntries(
          KPIS.flatMap((k) =>
            overall.previous && overall.previous[k.key] !== null && overall.previous[k.key] !== undefined
              ? [[k.key, formatMetric(k.key, overall.previous[k.key], currency)]]
              : [],
          ),
        )
      : {},
    incidents,
    incidentsTotal: analysis?.incidents.length ?? 0,
    incidentsHref: `${base}/datasets/${dataset.id}/analytics`,
    through: formatDate(dataset.date_max, true),
    weekly:
      points.length > 1
        ? {
            weeks: points.map((p) => p.week),
            series: Object.fromEntries(
              [...new Set([...KPIS.map((k) => k.key), ...TREND])].map((m) => [
                m,
                {
                  key: m,
                  label: METRIC_LABELS[m] ?? m,
                  values: points.map((p) => num(p.values[m])),
                  text: points.map((p) => formatMetric(m, p.values[m], currency)),
                  short: points.map((p) => (money(m) ? formatMoneyWhole(p.values[m], currency) : formatMetric(m, p.values[m], currency))),
                },
              ]),
            ),
          }
        : null,
    segments: segments
      ? {
          customers: formatCount(segments.customers),
          repeatRate: overall ? formatPercent(overall.current.repeat_customer_rate) : null,
          rows: segments.segments.map((s) => ({
            key: s.segment,
            label: SEGMENT_LABELS[s.segment] ?? s.segment,
            portion: num(s.share) ?? 0,
            // Relative to the largest segment, so the bars use the tile's full width.
            share: largest > 0 ? (num(s.share) ?? 0) / largest : 0,
            shareText: formatPercent(s.share),
            count: formatCount(s.customers),
            revenue: formatMoneyWhole(s.monetary_365, currency),
          })),
        }
      : null,
    quality: {
      covers: `${formatDate(dataset.date_min)} – ${formatDate(dataset.date_max, true)}`,
      uploaded: formatDate(dataset.created_at),
      uploadedExact: formatDateTime(dataset.created_at),
      orderLines: formatCount(dataset.orders_rows),
      status: clean ? "Data looks healthy" : "Data needs a look",
      detail: clean ? null : `${formatCount(errors)} rows rejected, ${formatCount(warnings)} warnings`,
      clean,
      restrictions,
      reportHref: `${base}/datasets/${dataset.id}`,
    },
    investigations: input.investigations.map((i) => ({
      id: i.id,
      date: formatDate(i.created_at),
      status: investigationStatus(i.status),
      statusKey: i.status,
      href: `${base}/investigations/${i.id}`,
    })),
    investigationAction: action,
    plans: input.plans.map((p) => ({
      id: p.id,
      text: `${formatDate(p.created_at)} · ${investigationStatus(p.status)}`,
      href: `${base}/recovery-plans/${p.id}`,
    })),
    decisionsHref: `${base}/decisions`,
    investigationsHref: `${base}/investigations`,
  };
}
