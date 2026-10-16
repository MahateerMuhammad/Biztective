import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getBusiness, listDatasets } from "@/lib/api/server";

export const metadata: Metadata = { title: "Business" };

const roleLabels = { owner: "Owner", analyst: "Analyst", viewer: "Viewer" } as const;

export default async function BusinessPage({ params }: PageProps<"/businesses/[businessId]">) {
  const { businessId } = await params;
  const [business, datasets] = await Promise.all([
    getBusiness(businessId),
    listDatasets(businessId),
  ]);
  if (!business || !datasets) {
    notFound();
  }

  return (
    <div className="grid gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{business.name}</h1>
        <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted-foreground">
          <div className="flex gap-1">
            <dt>Your role:</dt>
            <dd>{roleLabels[business.role]}</dd>
          </div>
          <div className="flex gap-1">
            <dt>Currency:</dt>
            <dd>{business.currency}</dd>
          </div>
          <div className="flex gap-1">
            <dt>Time zone:</dt>
            <dd>{business.timezone}</dd>
          </div>
        </dl>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            <h2 className="text-base font-semibold">Datasets</h2>
          </CardTitle>
          {business.role !== "viewer" ? (
            <Link
              href={`/businesses/${businessId}/datasets/new`}
              className="text-sm font-medium underline-offset-4 hover:underline"
            >
              Upload dataset
            </Link>
          ) : null}
        </CardHeader>
        <CardContent>
          {datasets.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">No datasets yet.</p>
          ) : (
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Dataset versions</caption>
              <thead>
                <tr className="border-b">
                  <th scope="col" className="py-2 font-medium">Version</th>
                  <th scope="col" className="py-2 font-medium">Status</th>
                  <th scope="col" className="py-2 font-medium">Order lines</th>
                  <th scope="col" className="py-2 font-medium">Date range</th>
                </tr>
              </thead>
              <tbody>
                {datasets.items.map((dataset) => (
                  <tr key={dataset.id} className="border-b last:border-0">
                    <td className="py-2">
                      <Link
                        href={`/businesses/${businessId}/datasets/${dataset.id}`}
                        className="underline-offset-4 hover:underline"
                      >
                        v{dataset.version}
                      </Link>
                    </td>
                    <td className="py-2">{dataset.status.replace("_", " ")}</td>
                    <td className="py-2">{dataset.orders_rows?.toLocaleString("en-US") ?? "—"}</td>
                    <td className="py-2">
                      {dataset.date_min && dataset.date_max
                        ? `${dataset.date_min} – ${dataset.date_max}`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
