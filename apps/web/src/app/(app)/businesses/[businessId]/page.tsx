import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/auto-refresh";
import { HealthGrid } from "@/components/health/health-grid";
import { Database } from "lucide-react";

import { EmptyState, PageHeader, SectionCard } from "@/components/page";
import { buttonVariants } from "@/components/ui/button";
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
        <PageHeader title="Business health" />
        <SectionCard>
          <EmptyState
            icon={Database}
            title={processing ? "Your data is being checked" : "Start with your sales data"}
            action={
              !processing && canAct ? (
                <div className="flex flex-wrap justify-center gap-3">
                  <Link href={`/businesses/${businessId}/datasets/new`} className={buttonVariants()}>
                    Upload files (CSV)
                  </Link>
                  <Link href={`/businesses/${businessId}/datasets/connect`} className={buttonVariants({ variant: "outline" })}>
                    Connect a PostgreSQL database
                  </Link>
                </div>
              ) : null
            }
          >
            {processing
              ? "This page updates by itself when the check finishes."
              : canAct
                ? "Biztective needs orders, products, customers and returns — as CSV files or from a PostgreSQL database — to measure your business and find what changed."
                : "An owner or analyst can upload data."}
          </EmptyState>
        </SectionCard>
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
    <>
      {health.analysing ? <AutoRefresh intervalMs={3000} /> : null}
      <HealthGrid
        data={health}
        subtitle={health.analysing ? "Analysing your latest data…" : `Updated through ${health.through}`}
      />
    </>
  );
}
