import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Pager, cursorParam } from "@/components/pager";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getBusiness, listDatasets } from "@/lib/api/server";
import { formatCount } from "@/lib/format";

export const metadata: Metadata = { title: "Data" };

export default async function DatasetsPage({ params, searchParams }: PageProps<"/businesses/[businessId]/datasets">) {
  const { businessId } = await params;
  const cursor = cursorParam((await searchParams).cursor);
  const [business, datasets] = await Promise.all([getBusiness(businessId), listDatasets(businessId, cursor)]);
  if (!business || !datasets) notFound();
  return (
    <div className="grid gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">Data</h1>
      <Card>
        <CardHeader>
          <CardTitle>
            <h2 className="text-base font-semibold">Dataset versions</h2>
          </CardTitle>
          <CardDescription>Every upload is validated and kept as its own version.</CardDescription>
          {business.role !== "viewer" ? (
            <Link href={`/businesses/${businessId}/datasets/new`} className="text-sm font-medium underline-offset-4 hover:underline">
              Upload dataset
            </Link>
          ) : null}
        </CardHeader>
        <CardContent className="overflow-x-auto">
          {datasets.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">No datasets yet.</p>
          ) : (
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Dataset versions</caption>
              <thead>
                <tr className="border-b">
                  <th scope="col" className="py-2 font-medium">Version</th>
                  <th scope="col" className="py-2 font-medium">Status</th>
                  <th scope="col" className="py-2 text-right font-medium">Order lines</th>
                  <th scope="col" className="py-2 font-medium">Date range</th>
                  <th scope="col" className="py-2 font-medium">Uploaded</th>
                </tr>
              </thead>
              <tbody>
                {datasets.items.map((dataset) => (
                  <tr key={dataset.id} className="border-b last:border-0">
                    <td className="py-2">
                      <Link href={`/businesses/${businessId}/datasets/${dataset.id}`} className="underline-offset-4 hover:underline">
                        v{dataset.version}
                      </Link>
                    </td>
                    <td className="py-2">{dataset.status.replaceAll("_", " ")}</td>
                    <td className="py-2 text-right tabular-nums">{formatCount(dataset.orders_rows)}</td>
                    <td className="py-2">{dataset.date_min && dataset.date_max ? `${dataset.date_min} – ${dataset.date_max}` : "—"}</td>
                    <td className="py-2">{dataset.created_at.slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <Pager path={`/businesses/${businessId}/datasets`} cursor={cursor} next={datasets.next_cursor ?? null} />
        </CardContent>
      </Card>
    </div>
  );
}
