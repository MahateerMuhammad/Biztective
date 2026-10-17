import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/auto-refresh";
import { HealthGrid } from "@/components/health/health-grid";
import { KindBadge } from "@/components/kind-badge";
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
import { buildHealth } from "@/lib/health";

export const metadata: Metadata = { title: "Business health" };

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
  const health = buildHealth({
    businessId,
    canAct,
    currency,
    dataset: ready,
    issues: detail?.issues ?? [],
    analysis: analysis ?? null,
    overall: comparison?.data.find((r) => r.dimension === "overall"),
    comparisonRestrictions: comparison?.restrictions ?? [],
    weekly: (weekly?.data ?? [])
      .filter((r) => r.dimension === "overall" && r.week)
      .map((r) => ({ week: r.week, values: r.metrics })),
    segments: segments?.data ?? null,
    investigations: investigations.items,
    plans: plans.items,
  });

  return (
    <div className="grid gap-5">
      {health.analysing ? <AutoRefresh intervalMs={3000} /> : null}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Business health</h1>
          <p className="mt-1 text-sm text-muted-foreground" role="status" aria-live="polite">
            {health.analysing
              ? "Analysing your latest data…"
              : `Data through ${ready.date_max ?? "—"} · analysed as of ${analysis?.as_of ?? "—"} · dataset v${ready.version}`}
          </p>
        </div>
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <KindBadge kind="fact" /> Every number on this page is measured from your uploaded records.
        </p>
      </div>
      <HealthGrid data={health} />
    </div>
  );
}
