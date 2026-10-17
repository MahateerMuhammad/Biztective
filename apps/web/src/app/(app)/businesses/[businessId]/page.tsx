import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/auto-refresh";
import { TrendChart } from "@/components/charts/trend-chart";
import { KindBadge } from "@/components/kind-badge";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  getAnalysis,
  getBusiness,
  getComparison,
  getDataset,
  getSegments,
  getWeeklyMetrics,
  listDatasets,
  pageInvestigations,
  pagePlans,
} from "@/lib/api/server";
import type { ComparisonRow, IncidentSummary } from "@/lib/api/types";
import { METRIC_LABELS, formatChange, formatCount, formatMetric, formatPercent } from "@/lib/format";

import { StartInvestigation } from "./datasets/[datasetId]/analytics/start-investigation";

export const metadata: Metadata = { title: "Business health" };

/** Headline metrics, and whether a rise is good news for the business. */
const KPIS: { key: string; up: "good" | "bad" }[] = [
  { key: "net_revenue", up: "good" },
  { key: "gross_margin_pct", up: "good" },
  { key: "refund_rate", up: "bad" },
  { key: "orders", up: "good" },
  { key: "aov", up: "good" },
  { key: "active_customers", up: "good" },
];
const TREND = ["net_revenue", "refund_rate", "discount_rate", "orders", "active_customers"];
const SEVERITY: Record<IncidentSummary["severity"], string> = {
  critical: "border-risk-foreground/30 bg-risk text-risk-foreground",
  high: "border-risk-foreground/30 bg-risk text-risk-foreground",
  medium: "border-hypothesis-foreground/30 bg-hypothesis text-hypothesis-foreground",
  low: "border-border bg-muted text-foreground",
};
const SEGMENTS: Record<string, string> = {
  loyal: "Loyal",
  at_risk: "At risk",
  dormant: "Dormant",
  insufficient_history: "Too new to tell",
};

function Kpi({ row, metric, up, currency }: { row: ComparisonRow; metric: string; up: "good" | "bad"; currency: string }) {
  const change = row.change?.[metric];
  const delta = change?.abs === null || change?.abs === undefined ? null : Number(change.abs);
  const direction = delta === null || delta === 0 ? "flat" : (delta > 0) === (up === "good") ? "better" : "worse";
  return (
    <div className="grid gap-1 rounded-lg border bg-card p-3">
      <dt className="text-xs font-medium text-muted-foreground">{METRIC_LABELS[metric] ?? metric}</dt>
      <dd className="text-xl font-semibold tabular-nums">{formatMetric(metric, row.current[metric], currency)}</dd>
      <dd
        className={
          direction === "worse"
            ? "text-xs font-medium text-risk-foreground"
            : direction === "better"
              ? "text-xs font-medium text-action-foreground"
              : "text-xs text-muted-foreground"
        }
      >
        {formatChange(metric, change, currency)} vs previous period
        <span className="sr-only"> ({direction === "flat" ? "unchanged" : direction})</span>
      </dd>
    </div>
  );
}

function incidentLabel(i: IncidentSummary): string {
  const metric = METRIC_LABELS[i.metric] ?? i.metric;
  return i.dimension === "overall" ? `${metric} across the business` : `${metric} in ${i.dimension} “${i.value}”`;
}

export default async function BusinessHealthPage({ params }: PageProps<"/businesses/[businessId]">) {
  const { businessId } = await params;
  const [business, datasets, investigations, plans] = await Promise.all([
    getBusiness(businessId),
    listDatasets(businessId, undefined, 20),
    pageInvestigations(businessId, undefined, 5),
    pagePlans(businessId, undefined, 5),
  ]);
  if (!business || !datasets) notFound();
  const canAct = business.role !== "viewer";
  const ready = datasets.items.find((d) => d.status === "ready");
  const processing = datasets.items.find((d) => d.status !== "ready" && d.status !== "failed" && d.status !== "rejected");

  if (!ready) {
    return (
      <div className="grid gap-6">
        {processing ? <AutoRefresh intervalMs={3000} /> : null}
        <h1 className="text-2xl font-semibold tracking-tight">Business health</h1>
        <Card>
          <CardHeader>
            <CardTitle>
              <h2 className="text-base font-semibold">{processing ? "Your data is being checked" : "Start with your sales data"}</h2>
            </CardTitle>
            <CardDescription>
              {processing
                ? `Dataset v${processing.version} is ${processing.status.replaceAll("_", " ")}. This page updates by itself.`
                : "ROOT needs orders, products, customers and returns (CSV) to measure your business and find what changed."}
            </CardDescription>
          </CardHeader>
          <CardContent className="text-sm">
            {canAct ? (
              <Link
                href={`/businesses/${businessId}/datasets/new`}
                className="inline-flex rounded-lg bg-primary px-3 py-2 font-medium text-primary-foreground hover:bg-primary/80"
              >
                Upload dataset
              </Link>
            ) : (
              <p className="text-muted-foreground">An owner or analyst can upload data.</p>
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  const [detail, analysis, comparison, weekly, segments] = await Promise.all([
    getDataset(businessId, ready.id),
    getAnalysis(businessId, ready.id),
    getComparison(businessId, ready.id),
    getWeeklyMetrics(businessId, ready.id),
    getSegments(businessId, ready.id),
  ]);
  const currency = detail?.currency ?? business.currency;
  const analysing = !analysis || analysis.status === "queued" || analysis.status === "running";
  const overall = comparison?.data.find((r) => r.dimension === "overall");
  const points = (weekly?.data ?? [])
    .filter((r) => r.dimension === "overall" && r.week)
    .map((r) => ({ week: r.week, values: r.metrics }))
    .slice(-26);
  const incidents = [...(analysis?.incidents ?? [])].sort((a, b) => a.rank - b.rank);
  const datasetInvestigations = investigations.items.filter((i) => i.dataset_id === ready.id);
  const openInvestigation = datasetInvestigations[0];
  const issues = detail?.issues ?? [];
  const errors = issues.filter((i) => i.severity === "error").reduce((n, i) => n + i.count, 0);
  const warnings = issues.filter((i) => i.severity !== "error").reduce((n, i) => n + i.count, 0);
  const restrictions = [...(analysis?.restrictions ?? []), ...(comparison?.restrictions ?? [])].filter(
    (r, i, all) => all.findIndex((x) => x.code === r.code) === i,
  );

  return (
    <div className="grid gap-6">
      {analysing ? <AutoRefresh intervalMs={3000} /> : null}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Business health</h1>
          <p className="mt-1 text-sm text-muted-foreground" role="status" aria-live="polite">
            {analysing
              ? "Analysing your latest data…"
              : `Data through ${ready.date_max ?? "—"} · analysed as of ${analysis?.as_of ?? "—"} · dataset v${ready.version}`}
          </p>
        </div>
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <KindBadge kind="fact" /> Every number on this page is measured from your uploaded records.
        </p>
      </div>

      {overall ? (
        <section aria-labelledby="kpi-heading" className="grid gap-3">
          <h2 id="kpi-heading" className="sr-only">
            Key metrics
          </h2>
          <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            {KPIS.map((k) => (
              <Kpi key={k.key} row={overall} metric={k.key} up={k.up} currency={currency} />
            ))}
          </dl>
        </section>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle>
              <h2 className="text-base font-semibold">Prioritized incidents</h2>
            </CardTitle>
            <CardDescription>
              Changes in the last 4 complete weeks that are unusual against the 12 before, ranked by estimated impact.
              Impacts overlap and must not be added together.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 text-sm">
            {analysing ? <p>Detection is running…</p> : null}
            {!analysing && analysis?.status === "insufficient_data" ? (
              <p>Not enough weeks of history yet to detect incidents reliably.</p>
            ) : null}
            {!analysing && analysis?.status === "completed" && incidents.length === 0 ? (
              <p>No material anomalies were found. Nothing needs investigating right now.</p>
            ) : null}
            {incidents.length > 0 ? (
              <ol className="grid gap-2" aria-label="Incidents by priority">
                {incidents.slice(0, 8).map((i) => (
                  <li key={i.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3">
                    <div className="grid gap-1">
                      <p className="flex flex-wrap items-center gap-2 font-medium">
                        <span className="text-muted-foreground tabular-nums">#{i.rank}</span>
                        <Badge className={SEVERITY[i.severity]}>{i.severity}</Badge>
                        {incidentLabel(i)}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        Now {formatMetric(i.metric, i.current_value, currency)} vs usual{" "}
                        {formatMetric(i.metric, i.baseline_median, currency)} · estimated impact{" "}
                        <span className="font-medium text-foreground">{formatMetric("net_revenue", i.impact, currency)}</span>
                        {i.onset_week ? ` · since week of ${i.onset_week}` : ""}
                      </p>
                    </div>
                    <Link
                      href={`/businesses/${businessId}/datasets/${ready.id}/analytics#incident-${i.id}`}
                      className="text-xs font-medium underline-offset-4 hover:underline"
                    >
                      Drill down<span className="sr-only"> into incident {i.rank}</span>
                    </Link>
                  </li>
                ))}
              </ol>
            ) : null}
            {incidents.length > 0 ? (
              <div className="flex flex-wrap items-center gap-3 border-t pt-3">
                {openInvestigation ? (
                  <Link
                    href={`/businesses/${businessId}/investigations/${openInvestigation.id}`}
                    className="inline-flex rounded-lg bg-primary px-3 py-2 font-medium text-primary-foreground hover:bg-primary/80"
                  >
                    {openInvestigation.status === "awaiting_human" ? "Answer ROOT's question" : "Open the investigation"}
                  </Link>
                ) : canAct ? (
                  <StartInvestigation businessId={businessId} datasetId={ready.id} />
                ) : null}
                <p className="text-xs text-muted-foreground">
                  ROOT tests competing explanations against your data and asks you when only you can know.
                </p>
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>
              <h2 className="text-base font-semibold">Data freshness and quality</h2>
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 text-sm">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
              <dt className="text-muted-foreground">Covers</dt>
              <dd>
                {ready.date_min} – {ready.date_max}
              </dd>
              <dt className="text-muted-foreground">Uploaded</dt>
              <dd>{ready.created_at.slice(0, 16).replace("T", " ")}</dd>
              <dt className="text-muted-foreground">Order lines</dt>
              <dd className="tabular-nums">{formatCount(ready.orders_rows)}</dd>
              <dt className="text-muted-foreground">Quality</dt>
              <dd>
                {errors === 0 && warnings === 0
                  ? "No issues found"
                  : `${formatCount(errors)} rows rejected · ${formatCount(warnings)} warnings`}
              </dd>
            </dl>
            {restrictions.length > 0 ? (
              <Alert>
                <AlertTitle>Limits on what the data can show</AlertTitle>
                <AlertDescription>
                  <ul className="list-disc pl-4">
                    {restrictions.slice(0, 4).map((r) => (
                      <li key={r.code}>{r.message}</li>
                    ))}
                  </ul>
                </AlertDescription>
              </Alert>
            ) : null}
            <Link href={`/businesses/${businessId}/datasets/${ready.id}`} className="font-medium underline-offset-4 hover:underline">
              Quality report for dataset v{ready.version}
            </Link>
          </CardContent>
        </Card>
      </div>

      {points.length > 1 ? (
        <Card>
          <CardHeader>
            <CardTitle>
              <h2 className="text-base font-semibold">Weekly trend</h2>
            </CardTitle>
            <CardDescription>Last {points.length} weeks, whole business.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3">
            <TrendChart points={points} metrics={TREND} currency={currency} />
            <details className="text-sm">
              <summary className="cursor-pointer font-medium underline-offset-4 hover:underline">Show the weekly numbers as a table</summary>
              <div tabIndex={0} role="region" aria-label="Weekly numbers" className="mt-2 max-h-80 overflow-auto">
                <table className="w-full text-left text-xs">
                  <caption className="sr-only">Weekly metrics, whole business</caption>
                  <thead>
                    <tr className="border-b">
                      <th scope="col" className="py-1 font-medium">Week of</th>
                      {TREND.map((m) => (
                        <th key={m} scope="col" className="py-1 text-right font-medium">
                          {METRIC_LABELS[m] ?? m}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {points.map((p) => (
                      <tr key={p.week} className="border-b last:border-0">
                        <th scope="row" className="py-1 font-normal">
                          {p.week}
                        </th>
                        {TREND.map((m) => (
                          <td key={m} className="py-1 text-right tabular-nums">
                            {formatMetric(m, p.values[m], currency)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-2">
        {segments ? (
          <Card>
            <CardHeader>
              <CardTitle>
                <h2 className="text-base font-semibold">Customer intelligence</h2>
              </CardTitle>
              <CardDescription>
                {formatCount(segments.data.customers)} customers by buying pattern
                {overall ? ` · repeat rate ${formatPercent(overall.current.repeat_customer_rate)}` : ""}.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <table className="w-full text-left text-sm">
                <caption className="sr-only">Customers by lifecycle segment</caption>
                <thead>
                  <tr className="border-b">
                    <th scope="col" className="py-2 font-medium">Segment</th>
                    <th scope="col" className="py-2 font-medium">Share of customers</th>
                    <th scope="col" className="py-2 text-right font-medium">Net revenue (365 days)</th>
                  </tr>
                </thead>
                <tbody>
                  {segments.data.segments.map((s) => (
                    <tr key={s.segment} className="border-b last:border-0">
                      <th scope="row" className="py-2 font-normal">
                        {SEGMENTS[s.segment] ?? s.segment}
                      </th>
                      <td className="py-2">
                        <div className="flex items-center gap-2">
                          <div className="h-2 w-24 rounded-full bg-muted" aria-hidden="true">
                            <div className="h-2 rounded-full bg-fact-foreground" style={{ width: `${Math.round(Number(s.share ?? 0) * 100)}%` }} />
                          </div>
                          <span className="tabular-nums">
                            {formatPercent(s.share)} ({formatCount(s.customers)})
                          </span>
                        </div>
                      </td>
                      <td className="py-2 text-right tabular-nums">{formatMetric("net_revenue", s.monetary_365, currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle>
              <h2 className="text-base font-semibold">Recent work</h2>
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 text-sm">
            <div className="grid gap-1">
              <h3 className="font-medium">Investigations</h3>
              {investigations.items.length === 0 ? (
                <p className="text-muted-foreground">None yet.</p>
              ) : (
                <ul className="grid gap-1">
                  {investigations.items.map((i) => (
                    <li key={i.id}>
                      <Link href={`/businesses/${businessId}/investigations/${i.id}`} className="underline-offset-4 hover:underline">
                        {i.created_at.slice(0, 10)} · {i.status.replaceAll("_", " ")}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="grid gap-1">
              <h3 className="font-medium">Decisions</h3>
              {plans.items.length === 0 ? (
                <p className="text-muted-foreground">No recovery plans yet.</p>
              ) : (
                <ul className="grid gap-1">
                  {plans.items.map((p) => (
                    <li key={p.id}>
                      <Link href={`/businesses/${businessId}/recovery-plans/${p.id}`} className="underline-offset-4 hover:underline">
                        {p.created_at.slice(0, 10)} · {p.status.replaceAll("_", " ")}
                        {p.execution_status ? ` · execution ${p.execution_status.replaceAll("_", " ")}` : ""}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
