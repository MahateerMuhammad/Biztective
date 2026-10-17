import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/auto-refresh";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  getAnalysis,
  getBusiness,
  getComparison,
  getDataset,
  getIncident,
  getProducts,
  getSegments,
  listInvestigations,
} from "@/lib/api/server";
import type { IncidentDetail, Restriction } from "@/lib/api/types";
import { METRIC_LABELS, formatChange, formatCount, formatMetric, formatPercent } from "@/lib/format";

import { StartInvestigation } from "./start-investigation";

export const metadata: Metadata = { title: "Analytics" };

const KPIS = [
  "net_revenue",
  "gross_profit",
  "gross_margin_pct",
  "refund_rate",
  "discount_rate",
  "orders",
  "aov",
  "active_customers",
  "repeat_customer_rate",
] as const;
const SEGMENT_LABELS: Record<string, string> = {
  loyal: "Loyal",
  at_risk: "At risk (overdue vs own cadence)",
  dormant: "Dormant",
  insufficient_history: "Insufficient history",
};

function Restrictions({ items }: { items: Restriction[] }) {
  if (items.length === 0) return null;
  return (
    <Alert>
      <AlertTitle>Data limitations</AlertTitle>
      <AlertDescription>
        <ul className="list-disc pl-4">
          {items.map((r) => (
            <li key={r.code}>
              <span className="font-mono text-xs">{r.code}</span>: {r.message}
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}

function Evidence({ incident, currency }: { incident: IncidentDetail; currency: string }) {
  const sources = incident.evidence.find((e) => e.kind === "source_records");
  const sample = (sources?.result.sample ?? []) as {
    file: string;
    source_row: number;
    key: string;
    amount: string;
    detail: string | null;
  }[];
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-sm underline-offset-4 hover:underline">
        Evidence ({incident.evidence.length} items, source records)
      </summary>
      <ul className="mt-2 text-xs text-muted-foreground">
        {incident.evidence.map((e) => (
          <li key={e.id}>
            <span className="font-mono">{e.kind}</span> · sha256 {e.result_hash.slice(0, 16)}…
          </li>
        ))}
      </ul>
      {sample.length > 0 ? (
        <table className="mt-2 w-full text-left text-xs">
          <caption className="sr-only">Top contributing source records</caption>
          <thead>
            <tr className="border-b">
              <th scope="col" className="py-1 font-medium">File</th>
              <th scope="col" className="py-1 text-right font-medium">Row</th>
              <th scope="col" className="py-1 font-medium">Record</th>
              <th scope="col" className="py-1 text-right font-medium">Amount</th>
              <th scope="col" className="py-1 font-medium">Detail</th>
            </tr>
          </thead>
          <tbody>
            {sample.map((s) => (
              <tr key={`${s.file}-${s.source_row}`} className="border-b last:border-0">
                <td className="py-1">{s.file}.csv</td>
                <td className="py-1 text-right tabular-nums">{s.source_row}</td>
                <td className="py-1 font-mono">{s.key}</td>
                <td className="py-1 text-right tabular-nums">{formatMetric("refunds", s.amount, currency)}</td>
                <td className="py-1">{s.detail ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </details>
  );
}

export default async function AnalyticsPage({
  params,
}: PageProps<"/businesses/[businessId]/datasets/[datasetId]/analytics">) {
  const { businessId, datasetId } = await params;
  const [business, dataset] = await Promise.all([getBusiness(businessId), getDataset(businessId, datasetId)]);
  if (!business || !dataset) notFound();
  const currency = dataset.currency;
  const back = (
    <Link href={`/businesses/${businessId}/datasets/${datasetId}`} className="text-sm underline-offset-4 hover:underline">
      ← Dataset v{dataset.version}
    </Link>
  );
  if (dataset.status !== "ready") {
    return (
      <div className="grid gap-4">
        {back}
        <p>Analytics are available once the dataset is ready.</p>
      </div>
    );
  }

  const [analysis, comparison, products, segments, investigations] = await Promise.all([
    getAnalysis(businessId, datasetId),
    getComparison(businessId, datasetId),
    getProducts(businessId, datasetId),
    getSegments(businessId, datasetId),
    listInvestigations(businessId, datasetId),
  ]);
  const incidents = analysis
    ? (await Promise.all(analysis.incidents.map((i) => getIncident(businessId, datasetId, i.id)))).filter(
        (i): i is IncidentDetail => i !== null,
      )
    : [];
  const running = !analysis || analysis.status === "queued" || analysis.status === "running";
  const overall = comparison?.data.find((r) => r.dimension === "overall");
  const period = comparison?.period as
    | { current: { from: string; to: string }; previous: { from: string; to: string; available: boolean } }
    | undefined;
  const restrictions = [
    ...(analysis?.restrictions ?? []),
    ...(comparison?.restrictions ?? []).filter((r) => !(analysis?.restrictions ?? []).some((a) => a.code === r.code)),
  ];

  return (
    <div className="grid gap-6">
      {running ? <AutoRefresh /> : null}
      <div>
        {back}
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">Analytics</h1>
        <p className="mt-1 text-sm" role="status" aria-live="polite">
          {running
            ? "Analysis in progress…"
            : analysis?.status === "insufficient_data"
              ? `Analysed as of ${analysis.as_of ?? "—"}; not enough history for incident detection.`
              : analysis?.status === "failed"
                ? "The analysis failed and was rolled back."
                : `Analysed as of ${analysis?.as_of}. All figures are computed from stored records.`}
        </p>
      </div>

      <Restrictions items={restrictions} />

      {!running && analysis?.status !== "failed" ? (
        <Card>
          <CardHeader>
            <CardTitle>
              <h2 className="text-base font-semibold">Investigations</h2>
            </CardTitle>
            <CardDescription>
              ROOT explains the incidents with evidence and asks you for context the data cannot
              show.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3">
            {business.role !== "viewer" ? (
              <StartInvestigation businessId={businessId} datasetId={datasetId} />
            ) : null}
            {investigations.length > 0 ? (
              <ul className="grid gap-1 text-sm" aria-label="Investigations of this dataset">
                {investigations.map((i) => (
                  <li key={i.id}>
                    <Link
                      href={`/businesses/${businessId}/investigations/${i.id}`}
                      className="underline-offset-4 hover:underline"
                    >
                      Investigation of {i.created_at.slice(0, 10)}: {i.status.replaceAll("_", " ")}
                    </Link>
                  </li>
                ))}
              </ul>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {overall && period ? (
        <Card>
          <CardHeader>
            <CardTitle>
              <h2 className="text-base font-semibold">Equal-period comparison</h2>
            </CardTitle>
            <CardDescription>
              {period.current.from} – {period.current.to} versus{" "}
              {period.previous.available ? `${period.previous.from} – ${period.previous.to}` : "no comparable earlier period"}
            </CardDescription>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Key metrics, current versus previous period</caption>
              <thead>
                <tr className="border-b">
                  <th scope="col" className="py-2 font-medium">Metric</th>
                  <th scope="col" className="py-2 text-right font-medium">Current</th>
                  <th scope="col" className="py-2 text-right font-medium">Previous</th>
                  <th scope="col" className="py-2 text-right font-medium">Change</th>
                </tr>
              </thead>
              <tbody>
                {KPIS.map((k) => (
                  <tr key={k} className="border-b last:border-0">
                    <th scope="row" className="py-2 font-normal">{METRIC_LABELS[k]}</th>
                    <td className="py-2 text-right tabular-nums">{formatMetric(k, overall.current[k], currency)}</td>
                    <td className="py-2 text-right tabular-nums">
                      {overall.previous ? formatMetric(k, overall.previous[k], currency) : "—"}
                    </td>
                    <td className="py-2 text-right tabular-nums">{formatChange(k, overall.change?.[k], currency)}</td>
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
            <h2 className="text-base font-semibold">Incidents</h2>
          </CardTitle>
          <CardDescription>
            Deterministic detection: last 4 complete weeks against the previous 12. Impacts are per
            incident and must not be added together.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {analysis?.status === "completed" && incidents.length === 0 ? (
            <p className="text-sm">No material anomalies were found.</p>
          ) : null}
          {incidents.length > 0 ? (
            <ol className="grid gap-4">
              {incidents.map((i) => (
                <li key={i.id} id={`incident-${i.id}`} className="scroll-mt-4 rounded-lg border p-3">
                  <p className="text-sm font-medium">
                    #{i.rank} · <span className="uppercase">{i.severity}</span> ·{" "}
                    {METRIC_LABELS[i.metric] ?? i.metric} in {i.dimension === "overall" ? "the whole business" : `${i.dimension} “${i.value}”`}
                  </p>
                  <p className="mt-1 text-sm">
                    {formatMetric(i.metric, i.stats.current_value as string, currency)} vs a baseline median of{" "}
                    {formatMetric(i.metric, i.stats.baseline_median as string, currency)}
                    {i.stats.z !== null ? `, robust z ${String(i.stats.z)}` : ""}. Estimated impact{" "}
                    {formatMetric("net_revenue", i.impact, currency)}.
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Records in scope: {formatCount(i.affected.order_lines)} order lines,{" "}
                    {formatCount(i.affected.orders)} orders, {formatCount(i.affected.customers)} customers,{" "}
                    {formatCount(i.affected.returns)} returns.
                  </p>
                  <Evidence incident={i} currency={currency} />
                </li>
              ))}
            </ol>
          ) : null}
        </CardContent>
      </Card>

      {segments ? (
        <Card>
          <CardHeader>
            <CardTitle>
              <h2 className="text-base font-semibold">Customer segments</h2>
            </CardTitle>
            <CardDescription>{segments.data.rules.note}</CardDescription>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Customers by lifecycle segment</caption>
              <thead>
                <tr className="border-b">
                  <th scope="col" className="py-2 font-medium">Segment</th>
                  <th scope="col" className="py-2 text-right font-medium">Customers</th>
                  <th scope="col" className="py-2 text-right font-medium">Share</th>
                  <th scope="col" className="py-2 text-right font-medium">Net revenue (365 days)</th>
                </tr>
              </thead>
              <tbody>
                {segments.data.segments.map((s) => (
                  <tr key={s.segment} className="border-b last:border-0">
                    <th scope="row" className="py-2 font-normal">{SEGMENT_LABELS[s.segment] ?? s.segment}</th>
                    <td className="py-2 text-right tabular-nums">{formatCount(s.customers)}</td>
                    <td className="py-2 text-right tabular-nums">{formatPercent(s.share)}</td>
                    <td className="py-2 text-right tabular-nums">{formatMetric("net_revenue", s.monetary_365, currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      ) : null}

      {products ? (
        <Card>
          <CardHeader>
            <CardTitle>
              <h2 className="text-base font-semibold">Top products</h2>
            </CardTitle>
            <CardDescription>By net revenue over the whole dataset.</CardDescription>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Product performance and return rates</caption>
              <thead>
                <tr className="border-b">
                  <th scope="col" className="py-2 font-medium">Product</th>
                  <th scope="col" className="py-2 font-medium">Category</th>
                  <th scope="col" className="py-2 text-right font-medium">Net revenue</th>
                  <th scope="col" className="py-2 text-right font-medium">Gross margin</th>
                  <th scope="col" className="py-2 text-right font-medium">Unit return rate</th>
                  <th scope="col" className="py-2 text-right font-medium">Refund rate</th>
                </tr>
              </thead>
              <tbody>
                {products.data.map((p) => (
                  <tr key={p.product_id} className="border-b last:border-0">
                    <th scope="row" className="py-2 font-normal">
                      {p.product_name} <span className="text-xs text-muted-foreground">({p.product_id})</span>
                    </th>
                    <td className="py-2">{p.category}</td>
                    <td className="py-2 text-right tabular-nums">{formatMetric("net_revenue", p.metrics.net_revenue, currency)}</td>
                    <td className="py-2 text-right tabular-nums">{formatPercent(p.metrics.gross_margin_pct)}</td>
                    <td className="py-2 text-right tabular-nums">{formatPercent(p.metrics.unit_return_rate)}</td>
                    <td className="py-2 text-right tabular-nums">{formatPercent(p.metrics.refund_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
