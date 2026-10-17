import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { EmptyState, PageHeader, SectionCard, StatusChip, rowLinkClass, tableClass, tableWrap, tdClass, thClass, trClass } from "@/components/page";
import { Pager, cursorParam } from "@/components/pager";
import { buttonVariants } from "@/components/ui/button";
import { getBusiness, listDatasets } from "@/lib/api/server";
import { formatCount, formatDate, formatDateTime } from "@/lib/format";
import { datasetStatus } from "@/lib/status";
import { Database } from "lucide-react";

export const metadata: Metadata = { title: "Data" };

export default async function DatasetsPage({ params, searchParams }: PageProps<"/businesses/[businessId]/datasets">) {
  const { businessId } = await params;
  const cursor = cursorParam((await searchParams).cursor);
  const [business, datasets] = await Promise.all([getBusiness(businessId), listDatasets(businessId, cursor)]);
  if (!business || !datasets) notFound();
  const upload = business.role !== "viewer" ? `/businesses/${businessId}/datasets/new` : null;
  return (
    <div className="grid gap-6">
      <PageHeader
        title="Data"
        subtitle="Every upload is checked and kept as its own version."
        action={upload ? <Link href={upload} className={buttonVariants()}>Upload data</Link> : null}
      />
      <SectionCard>
        {datasets.items.length === 0 ? (
          <EmptyState icon={Database} title="No data yet" action={upload ? <Link href={upload} className={buttonVariants()}>Upload data</Link> : null}>
            Upload your orders, products, customers and returns to get started.
          </EmptyState>
        ) : (
          <div className={tableWrap} tabIndex={0} role="region" aria-label="Table, scrollable">
            <table className={tableClass}>
              <caption className="sr-only">Data versions, newest first</caption>
              <thead>
                <tr>
                  <th scope="col" className={thClass}>Version</th>
                  <th scope="col" className={thClass}>Status</th>
                  <th scope="col" className={`${thClass} text-right`}>Order lines</th>
                  <th scope="col" className={thClass}>Covers</th>
                  <th scope="col" className={thClass}>Uploaded</th>
                </tr>
              </thead>
              <tbody>
                {datasets.items.map((dataset) => {
                  const status = datasetStatus(dataset.status);
                  return (
                    <tr key={dataset.id} className={trClass}>
                      <td className={tdClass}>
                        <Link href={`/businesses/${businessId}/datasets/${dataset.id}`} className={rowLinkClass}>
                          Version {dataset.version}
                        </Link>
                      </td>
                      <td className={tdClass}><StatusChip tone={status.tone}>{status.label}</StatusChip></td>
                      <td className={`${tdClass} text-right tabular-nums`}>{formatCount(dataset.orders_rows)}</td>
                      <td className={`${tdClass} whitespace-nowrap`}>
                        {dataset.date_min && dataset.date_max ? `${formatDate(dataset.date_min)} – ${formatDate(dataset.date_max, true)}` : "—"}
                      </td>
                      <td className={`${tdClass} whitespace-nowrap`} title={formatDateTime(dataset.created_at)}>{formatDate(dataset.created_at, true)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <Pager path={`/businesses/${businessId}/datasets`} cursor={cursor} next={datasets.next_cursor ?? null} />
      </SectionCard>
    </div>
  );
}
