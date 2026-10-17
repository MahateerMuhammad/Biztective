import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/auto-refresh";
import { Disclosure, EmptyState, PageHeader, SectionCard, StatusChip, tableClass, tableWrap, tdClass, thClass, trClass } from "@/components/page";
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
import { METRIC_LABELS, formatChange, formatCount, formatDate, formatMetric, formatMoneyCompact, formatPercent } from "@/lib/format";
import { investigationChip } from "@/lib/status";
import type { InvestigationStatus } from "@/lib/api/types";

import { StartInvestigation } from "./start-investigation";

export const metadata: Metadata = { title: "Analytics" };

const SEVERITY: Record<string, { label: string; tone: "err" | "warn" | "idle" }> = {
  critical: { label: "Critical", tone: "err" },
  high: { label: "High", tone: "err" },
  medium: { label: "Medium", tone: "warn" },
  low: { label: "Low", tone: "idle" },
};

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
  at_risk: "At risk",
  dormant: "Dormant",
  insufficient_history: "Too new to tell",
};

function Restrictions({ items }: { items: Restriction[] }) {
  if (items.length === 0) return null;
  return (
    <Disclosure summary="What this can’t show" className="rounded-xl bg-card px-6 py-2 ring-1 ring-foreground/10">
      <ul className="list-disc pl-5">
        {items.map((r) => (
          <li key={r.code}>
            {r.message}
            <span className="sr-only"> (code {r.code})</span>
          </li>
        ))}
      </ul>
    </Disclosure>
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
    <Disclosure summary={`Show the evidence (${incident.evidence.length} checks)`}>
      <ul className="mt-1 grid gap-0.5 text-sm">
        {incident.evidence.map((e) => (
          <li key={e.id}>
            {e.kind.replaceAll("_", " ")} <span className="text-muted-foreground">· fingerprint {e.result_hash.slice(0, 12)}…</span>
          </li>
        ))}
      </ul>
      {sample.length > 0 ? (
        <div className={`${tableWrap} mt-3`} tabIndex={0} role="region" aria-label="Table, scrollable">
          <table className={tableClass}>
            <caption className="sr-only">Top contributing source records</caption>
            <thead>
              <tr>
                <th scope="col" className={thClass}>File</th>
                <th scope="col" className={`${thClass} text-right`}>Row</th>
                <th scope="col" className={thClass}>Record</th>
                <th scope="col" className={`${thClass} text-right`}>Amount</th>
                <th scope="col" className={thClass}>Detail</th>
              </tr>
            </thead>
            <tbody>
              {sample.map((s) => (
                <tr key={`${s.file}-${s.source_row}`} className={trClass}>
                  <td className={`${tdClass} capitalize`}>{s.file}</td>
                  <td className={`${tdClass} text-right tabular-nums`}>{s.source_row}</td>
                  <td className={tdClass}>{s.key}</td>
                  <td className={`${tdClass} text-right tabular-nums`}>{formatMetric("refunds", s.amount, currency)}</td>
                  <td className={tdClass}>{s.detail ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Disclosure>
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
    <Link href={`/businesses/${businessId}/datasets/${datasetId}`} className="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
      ← Data version {dataset.version}
    </Link>
  );
  if (dataset.status !== "ready") {
    return (
      <div className="grid gap-6">
        <PageHeader back={back} title="Analytics" />
        <SectionCard>
          <EmptyState title="Analytics are not ready yet">They appear once the data check has finished.</EmptyState>
        </SectionCard>
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
      <PageHeader
        back={back}
        title="Analytics"
        subtitle={
          running
            ? "Analysing your data…"
            : analysis?.status === "insufficient_data"
              ? `Updated through ${formatDate(analysis.as_of, true)}. Not enough history yet to spot changes.`
              : analysis?.status === "failed"
                ? "The analysis couldn’t finish. Nothing was changed."
                : `Updated through ${formatDate(analysis?.as_of, true)}`
        }
        action={
          !running && analysis?.status !== "failed" && business.role !== "viewer" ? (
            <StartInvestigation businessId={businessId} datasetId={datasetId} />
          ) : null
        }
      />

      <Restrictions items={restrictions} />

      {overall && period ? (
        <SectionCard
          title="This period vs the last"
          help={`${formatDate(period.current.from)} – ${formatDate(period.current.to, true)} compared with ${
            period.previous.available
              ? `${formatDate(period.previous.from)} – ${formatDate(period.previous.to, true)}`
              : "an earlier period (none comparable yet)"
          }`}
        >
          <div className={tableWrap} tabIndex={0} role="region" aria-label="Table, scrollable">
            <table className={tableClass}>
              <caption className="sr-only">Key metrics, current versus previous period</caption>
              <thead>
                <tr>
                  <th scope="col" className={thClass}>Metric</th>
                  <th scope="col" className={`${thClass} text-right`}>Now</th>
                  <th scope="col" className={`${thClass} text-right`}>Before</th>
                  <th scope="col" className={`${thClass} text-right`}>Change</th>
                </tr>
              </thead>
              <tbody>
                {KPIS.map((k) => (
                  <tr key={k} className={trClass}>
                    <th scope="row" className={`${tdClass} font-normal`}>{METRIC_LABELS[k]}</th>
                    <td className={`${tdClass} text-right font-medium tabular-nums`}>{formatMetric(k, overall.current[k], currency)}</td>
                    <td className={`${tdClass} text-right tabular-nums text-muted-foreground`}>
                      {overall.previous ? formatMetric(k, overall.previous[k], currency) : "—"}
                    </td>
                    <td className={`${tdClass} text-right tabular-nums`}>{formatChange(k, overall.change?.[k], currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      ) : null}

      <SectionCard
        title="Biggest changes to look at"
        help="The last 4 complete weeks against the 12 before. Impacts overlap and must not be added together."
      >
        {analysis?.status === "completed" && incidents.length === 0 ? (
          <p className="text-[15px] text-muted-foreground">No material changes were found.</p>
        ) : null}
        {incidents.length > 0 ? (
          <ol className="grid gap-4">
            {incidents.map((i) => {
              const sev = SEVERITY[i.severity] ?? { label: i.severity, tone: "idle" as const };
              return (
                <li key={i.id} id={`incident-${i.id}`} className="scroll-mt-24 grid gap-2 rounded-xl border border-border p-4">
                  <p className="flex flex-wrap items-center gap-2 text-base font-medium">
                    <StatusChip tone={sev.tone}>{sev.label}</StatusChip>
                    {METRIC_LABELS[i.metric] ?? i.metric} in {i.dimension === "overall" ? "the whole business" : `${i.dimension} “${i.value}”`}
                  </p>
                  <p className="text-[15px]">
                    {formatMetric(i.metric, i.stats.current_value as string, currency)} now, against a usual{" "}
                    {formatMetric(i.metric, i.stats.baseline_median as string, currency)}. Impact about{" "}
                    <span className="font-medium" title={formatMetric("net_revenue", i.impact, currency)}>
                      {formatMoneyCompact(i.impact, currency)}
                    </span>
                    .
                    {i.onset_week ? ` Since the week of ${formatDate(i.onset_week)}.` : ""}
                    {i.stats.z !== null ? <span className="sr-only">{` Robust z-score ${String(i.stats.z)}.`}</span> : null}
                  </p>
                  <p className="text-sm text-muted-foreground">
                    Based on {formatCount(i.affected.order_lines)} order lines, {formatCount(i.affected.orders)} orders,{" "}
                    {formatCount(i.affected.customers)} customers and {formatCount(i.affected.returns)} returns.
                  </p>
                  <Evidence incident={i} currency={currency} />
                </li>
              );
            })}
          </ol>
        ) : null}
      </SectionCard>

      {investigations.length > 0 ? (
        <SectionCard title="Investigations of this data" help="Biztective explains changes with evidence and asks you for context the data can’t show.">
          <ul className="grid gap-1" aria-label="Investigations of this data">
            {investigations.map((i) => {
              const chip = investigationChip(i.status as InvestigationStatus);
              return (
                <li key={i.id}>
                  <Link
                    href={`/businesses/${businessId}/investigations/${i.id}`}
                    className="-mx-2 flex min-h-11 items-center justify-between gap-3 rounded-lg px-2 text-[15px] hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
                  >
                    <span>Investigation of {formatDate(i.created_at, true)}</span>
                    <StatusChip tone={chip.tone}>{chip.label}</StatusChip>
                  </Link>
                </li>
              );
            })}
          </ul>
        </SectionCard>
      ) : null}

      {segments ? (
        <SectionCard title="Customer segments" help={segments.data.rules.note}>
          <div className={tableWrap} tabIndex={0} role="region" aria-label="Table, scrollable">
            <table className={tableClass}>
              <caption className="sr-only">Customers by lifecycle segment</caption>
              <thead>
                <tr>
                  <th scope="col" className={thClass}>Segment</th>
                  <th scope="col" className={`${thClass} text-right`}>Customers</th>
                  <th scope="col" className={`${thClass} text-right`}>Share</th>
                  <th scope="col" className={`${thClass} text-right`}>Net revenue (365 days)</th>
                </tr>
              </thead>
              <tbody>
                {segments.data.segments.map((s) => (
                  <tr key={s.segment} className={trClass}>
                    <th scope="row" className={`${tdClass} font-normal`}>{SEGMENT_LABELS[s.segment] ?? s.segment}</th>
                    <td className={`${tdClass} text-right tabular-nums`}>{formatCount(s.customers)}</td>
                    <td className={`${tdClass} text-right tabular-nums`}>{formatPercent(s.share)}</td>
                    <td className={`${tdClass} text-right tabular-nums`}>{formatMetric("net_revenue", s.monetary_365, currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      ) : null}

      {products ? (
        <SectionCard title="Top products" help="By net revenue over all of this data.">
          <div className={tableWrap} tabIndex={0} role="region" aria-label="Table, scrollable">
            <table className={tableClass}>
              <caption className="sr-only">Product performance and return rates</caption>
              <thead>
                <tr>
                  <th scope="col" className={thClass}>Product</th>
                  <th scope="col" className={thClass}>Category</th>
                  <th scope="col" className={`${thClass} text-right`}>Net revenue</th>
                  <th scope="col" className={`${thClass} text-right`}>Gross margin</th>
                  <th scope="col" className={`${thClass} text-right`}>Returned</th>
                  <th scope="col" className={`${thClass} text-right`}>Refund rate</th>
                </tr>
              </thead>
              <tbody>
                {products.data.map((p) => (
                  <tr key={p.product_id} className={trClass}>
                    <th scope="row" className={`${tdClass} font-normal`} title={p.product_id}>
                      {p.product_name}
                    </th>
                    <td className={tdClass}>{p.category}</td>
                    <td className={`${tdClass} text-right tabular-nums`}>{formatMetric("net_revenue", p.metrics.net_revenue, currency)}</td>
                    <td className={`${tdClass} text-right tabular-nums`}>{formatPercent(p.metrics.gross_margin_pct)}</td>
                    <td className={`${tdClass} text-right tabular-nums`}>{formatPercent(p.metrics.unit_return_rate)}</td>
                    <td className={`${tdClass} text-right tabular-nums`}>{formatPercent(p.metrics.refund_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      ) : null}
    </div>
  );
}
