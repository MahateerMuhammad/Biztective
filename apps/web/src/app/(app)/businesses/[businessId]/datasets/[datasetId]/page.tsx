import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/auto-refresh";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getBusiness, getDataset, listIssues } from "@/lib/api/server";
import type { DatasetDetail, FileMapping } from "@/lib/api/types";

import { MappingReview } from "./mapping-review";

export const metadata: Metadata = { title: "Dataset" };

const STATUS_TEXT: Record<DatasetDetail["status"], string> = {
  awaiting_mapping: "Waiting for column mapping confirmation",
  queued: "Queued for validation",
  validating: "Validating…",
  ready: "Ready",
  rejected: "Rejected: data quality problems",
  failed: "Failed: no data was loaded",
};
const IN_PROGRESS = new Set<DatasetDetail["status"]>(["queued", "validating"]);

function n(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toLocaleString("en-US");
}

export default async function DatasetPage({
  params,
}: PageProps<"/businesses/[businessId]/datasets/[datasetId]">) {
  const { businessId, datasetId } = await params;
  const [business, dataset, issues] = await Promise.all([
    getBusiness(businessId),
    getDataset(businessId, datasetId),
    listIssues(businessId, datasetId),
  ]);
  if (!business || !dataset || !issues) {
    notFound();
  }
  const canUpload = business.role === "owner" || business.role === "analyst";
  const exportBase = `/api/v1/businesses/${businessId}/datasets/${datasetId}/export`;
  const errors = dataset.issues.filter((i) => i.severity === "error");

  return (
    <div className="grid gap-6">
      {IN_PROGRESS.has(dataset.status) ? <AutoRefresh /> : null}
      <div>
        <Link href={`/businesses/${businessId}`} className="text-sm underline-offset-4 hover:underline">
          ← {business.name}
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">Dataset v{dataset.version}</h1>
        <p className="mt-1 text-sm" role="status" aria-live="polite">
          <span className="font-medium">{STATUS_TEXT[dataset.status]}</span>
        </p>
        {dataset.status === "ready" ? (
          <p className="mt-2">
            <Link
              href={`/businesses/${businessId}/datasets/${datasetId}/analytics`}
              className="text-sm font-medium underline-offset-4 hover:underline"
            >
              View analytics
            </Link>
          </p>
        ) : null}
        <p className="mt-1 text-xs text-muted-foreground">
          Times read as {dataset.source_timezone}; dates stored in {dataset.business_timezone};
          currency {dataset.currency}; checksum {dataset.checksum_sha256?.slice(0, 12)}…
        </p>
      </div>

      {dataset.status === "failed" ? (
        <Alert variant="destructive">
          <AlertTitle>Processing failed and was rolled back</AlertTitle>
          <AlertDescription>
            No records from this upload were stored (reference {dataset.error_code}). You can upload
            the same files again.
          </AlertDescription>
        </Alert>
      ) : null}

      {dataset.status === "awaiting_mapping" ? (
        <Card>
          <CardHeader>
            <CardTitle>
              <h2 className="text-base font-semibold">Confirm column mapping</h2>
            </CardTitle>
            <CardDescription>
              Some headers did not match exactly. Nothing is loaded until you confirm which column
              holds each field.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {canUpload ? (
              <MappingReview
                businessId={businessId}
                datasetId={datasetId}
                files={dataset.files.map((f) => ({
                  kind: f.kind,
                  headers: f.headers,
                  mapping: f.mapping as unknown as FileMapping,
                }))}
              />
            ) : (
              <p className="text-sm text-muted-foreground">An owner or analyst must confirm the mapping.</p>
            )}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>
            <h2 className="text-base font-semibold">Files</h2>
          </CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Rows per file</caption>
            <thead>
              <tr className="border-b">
                <th scope="col" className="py-2 font-medium">File</th>
                <th scope="col" className="py-2 text-right font-medium">Rows</th>
                <th scope="col" className="py-2 text-right font-medium">Accepted</th>
                <th scope="col" className="py-2 text-right font-medium">Rejected</th>
                <th scope="col" className="py-2 text-right font-medium">Duplicates</th>
                <th scope="col" className="py-2 font-medium">Export</th>
              </tr>
            </thead>
            <tbody>
              {dataset.files.map((file) => (
                <tr key={file.kind} className="border-b last:border-0">
                  <td className="py-2">
                    {file.kind}.csv <span className="text-xs text-muted-foreground">({file.filename})</span>
                  </td>
                  <td className="py-2 text-right tabular-nums">{n(file.rows_total)}</td>
                  <td className="py-2 text-right tabular-nums">{n(file.rows_accepted)}</td>
                  <td className="py-2 text-right tabular-nums">{n(file.rows_rejected)}</td>
                  <td className="py-2 text-right tabular-nums">{n(file.rows_duplicate)}</td>
                  <td className="py-2">
                    {dataset.status === "ready" ? (
                      <a className="underline-offset-4 hover:underline" href={`${exportBase}/${file.kind}`}>
                        CSV<span className="sr-only"> of {file.kind}</span>
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>
            <h2 className="text-base font-semibold">Data quality report</h2>
          </CardTitle>
          <CardDescription>
            {dataset.issues.length === 0
              ? "No issues found."
              : `${errors.reduce((sum, i) => sum + i.count, 0).toLocaleString("en-US")} row errors and ${dataset.issues
                  .filter((i) => i.severity === "warning")
                  .reduce((sum, i) => sum + i.count, 0)
                  .toLocaleString("en-US")} warnings.`}{" "}
            <a className="underline-offset-4 hover:underline" href={`${exportBase}/issues`}>
              Download all issues (CSV)
            </a>
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-6 overflow-x-auto">
          {dataset.issues.length > 0 ? (
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Issue counts by file and rule</caption>
              <thead>
                <tr className="border-b">
                  <th scope="col" className="py-2 font-medium">File</th>
                  <th scope="col" className="py-2 font-medium">Rule</th>
                  <th scope="col" className="py-2 font-medium">Severity</th>
                  <th scope="col" className="py-2 font-medium">What it means</th>
                  <th scope="col" className="py-2 text-right font-medium">Count</th>
                </tr>
              </thead>
              <tbody>
                {dataset.issues.map((issue) => (
                  <tr key={`${issue.file}-${issue.code}`} className="border-b last:border-0">
                    <td className="py-2">{issue.file}</td>
                    <td className="py-2 font-mono">{issue.code}</td>
                    <td className={issue.severity === "error" ? "py-2 text-destructive" : "py-2"}>
                      {issue.severity}
                    </td>
                    <td className="py-2">{issue.description}</td>
                    <td className="py-2 text-right tabular-nums">{n(issue.count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {issues.items.length > 0 ? (
            <table className="w-full text-left text-sm">
              <caption className="pb-2 text-left text-sm font-medium">
                First {issues.items.length} issues
              </caption>
              <thead>
                <tr className="border-b">
                  <th scope="col" className="py-2 font-medium">File</th>
                  <th scope="col" className="py-2 text-right font-medium">Row</th>
                  <th scope="col" className="py-2 font-medium">Column</th>
                  <th scope="col" className="py-2 font-medium">Rule</th>
                  <th scope="col" className="py-2 font-medium">Message</th>
                  <th scope="col" className="py-2 font-medium">Value</th>
                </tr>
              </thead>
              <tbody>
                {issues.items.map((issue) => (
                  <tr key={issue.id} className="border-b last:border-0 align-top">
                    <td className="py-2">{issue.file_kind}</td>
                    <td className="py-2 text-right tabular-nums">{issue.row_number ?? "—"}</td>
                    <td className="py-2 font-mono">{issue.column_name ?? "—"}</td>
                    <td className="py-2 font-mono">{issue.code}</td>
                    <td className="py-2">{issue.message}</td>
                    <td className="py-2 font-mono break-all">{issue.excerpt ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
