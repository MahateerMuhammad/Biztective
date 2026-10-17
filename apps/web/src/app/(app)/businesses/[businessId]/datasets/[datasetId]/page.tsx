import { Badge } from "@/components/ui/badge";
import { isPostgresDataset } from "@/lib/api/types";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/auto-refresh";
import { Disclosure, PageHeader, SectionCard, StatusChip, tableClass, tableWrap, tdClass, thClass, trClass } from "@/components/page";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import { InfoTip } from "@/components/ui/info-tip";
import { getBusiness, getDataset, listIssues } from "@/lib/api/server";
import type { DatasetDetail, FileMapping } from "@/lib/api/types";
import { formatCount } from "@/lib/format";
import { datasetStatus } from "@/lib/status";

import { MappingReview } from "./mapping-review";

export const metadata: Metadata = { title: "Data check" };

const STATUS_TEXT: Record<DatasetDetail["status"], string> = {
  awaiting_mapping: "Waiting for you to confirm the columns",
  queued: "Waiting to be checked",
  validating: "Being checked…",
  ready: "Checked and ready to analyse",
  rejected: "Rejected: problems in the data",
  failed: "Couldn’t process, nothing was loaded",
};
const IN_PROGRESS = new Set<DatasetDetail["status"]>(["queued", "validating"]);

const n = formatCount;

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
  const errorCount = dataset.issues.filter((i) => i.severity === "error").reduce((sum, i) => sum + i.count, 0);
  const warningCount = dataset.issues.filter((i) => i.severity === "warning").reduce((sum, i) => sum + i.count, 0);
  const status = datasetStatus(dataset.status);

  return (
    <div className="grid gap-6">
      {IN_PROGRESS.has(dataset.status) ? <AutoRefresh /> : null}
      <PageHeader
        back={
          <Link href={`/businesses/${businessId}/datasets`} className="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
            ← Data
          </Link>
        }
        title={
          <span className="flex flex-wrap items-center gap-3">
            Data version {dataset.version}
            <StatusChip tone={status.tone}>{status.label}</StatusChip>
            {isPostgresDataset(dataset) ? <Badge>From PostgreSQL</Badge> : null}
          </span>
        }
        subtitle={
          <span className="inline-flex items-center gap-1">
            {STATUS_TEXT[dataset.status]}
            <InfoTip label="Technical details">
              Times are read as {dataset.source_timezone} and dates are stored in {dataset.business_timezone}. Amounts are in{" "}
              {dataset.currency}. Checksum {dataset.checksum_sha256?.slice(0, 12)}….
            </InfoTip>
          </span>
        }
        action={
          dataset.status === "ready" ? (
            <Link href={`/businesses/${businessId}/datasets/${datasetId}/analytics`} className={buttonVariants()}>
              View analytics
            </Link>
          ) : null
        }
      />

      {dataset.status === "failed" ? (
        <Alert variant="destructive" className="p-6">
          <AlertTitle className="text-base">We couldn’t process this upload</AlertTitle>
          <AlertDescription className="text-[15px]">
            Nothing from it was stored (reference {dataset.error_code}). You can upload the same files again.
          </AlertDescription>
        </Alert>
      ) : null}

      {dataset.status === "awaiting_mapping" ? (
        <SectionCard title="Confirm your columns" help="Some headers did not match exactly. Nothing is loaded until you confirm which column holds each field.">
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
            <p className="text-[15px] text-muted-foreground">An owner or analyst must confirm the columns.</p>
          )}
        </SectionCard>
      ) : null}

      <SectionCard title="Files">
        <div className={tableWrap} tabIndex={0} role="region" aria-label="Table, scrollable">
          <table className={tableClass}>
            <caption className="sr-only">Rows per file</caption>
            <thead>
              <tr>
                <th scope="col" className={thClass}>File</th>
                <th scope="col" className={`${thClass} text-right`}>Rows</th>
                <th scope="col" className={`${thClass} text-right`}>Accepted</th>
                <th scope="col" className={`${thClass} text-right`}>Rejected</th>
                <th scope="col" className={`${thClass} text-right`}>Duplicates</th>
                <th scope="col" className={thClass}>Download</th>
              </tr>
            </thead>
            <tbody>
              {dataset.files.map((file) => (
                <tr key={file.kind} className={trClass}>
                  <td className={`${tdClass} font-medium capitalize`} title={file.filename}>{file.kind}</td>
                  <td className={`${tdClass} text-right tabular-nums`}>{n(file.rows_total)}</td>
                  <td className={`${tdClass} text-right tabular-nums`}>{n(file.rows_accepted)}</td>
                  <td className={`${tdClass} text-right tabular-nums`}>{n(file.rows_rejected)}</td>
                  <td className={`${tdClass} text-right tabular-nums`}>{n(file.rows_duplicate)}</td>
                  <td className={tdClass}>
                    {dataset.status === "ready" ? (
                      <a className="inline-flex min-h-11 items-center underline underline-offset-4" href={`${exportBase}/${file.kind}`}>
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
        </div>
      </SectionCard>

      <SectionCard
        title="Data check"
        help={
          dataset.issues.length === 0
            ? "No problems found."
            : `${n(errorCount)} rows rejected and ${n(warningCount)} warnings.`
        }
        action={
          <a className="inline-flex min-h-11 items-center text-[15px] font-medium underline underline-offset-4" href={`${exportBase}/issues`}>
            Download all issues (CSV)
          </a>
        }
      >
        {dataset.issues.length > 0 ? (
          <div className={tableWrap} tabIndex={0} role="region" aria-label="Table, scrollable">
            <table className={tableClass}>
              <caption className="sr-only">Issue counts by file and rule</caption>
              <thead>
                <tr>
                  <th scope="col" className={thClass}>File</th>
                  <th scope="col" className={thClass}>Severity</th>
                  <th scope="col" className={thClass}>What it means</th>
                  <th scope="col" className={`${thClass} text-right`}>Count</th>
                </tr>
              </thead>
              <tbody>
                {dataset.issues.map((issue) => (
                  <tr key={`${issue.file}-${issue.code}`} className={trClass}>
                    <td className={`${tdClass} capitalize`} title={`Rule ${issue.code}`}>{issue.file}</td>
                    <td className={tdClass}>
                      <StatusChip tone={issue.severity === "error" ? "err" : "warn"}>{issue.severity === "error" ? "Rejected rows" : "Warning"}</StatusChip>
                    </td>
                    <td className={tdClass}>
                      {issue.description}
                      <span className="sr-only"> (rule {issue.code})</span>
                    </td>
                    <td className={`${tdClass} text-right tabular-nums`}>{n(issue.count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {issues.items.length > 0 ? (
          <Disclosure summary={`Show the first ${issues.items.length} issues`}>
            <div className={tableWrap} tabIndex={0} role="region" aria-label="Table, scrollable">
              <table className={tableClass}>
                <caption className="sr-only">First {issues.items.length} issues</caption>
                <thead>
                  <tr>
                    <th scope="col" className={thClass}>File</th>
                    <th scope="col" className={`${thClass} text-right`}>Row</th>
                    <th scope="col" className={thClass}>Column</th>
                    <th scope="col" className={thClass}>Message</th>
                    <th scope="col" className={thClass}>Value</th>
                  </tr>
                </thead>
                <tbody>
                  {issues.items.map((issue) => (
                    <tr key={issue.id} className={`${trClass} align-top`}>
                      <td className={`${tdClass} capitalize`} title={`Rule ${issue.code}`}>{issue.file_kind}</td>
                      <td className={`${tdClass} text-right tabular-nums`}>{issue.row_number ?? "—"}</td>
                      <td className={tdClass}>{issue.column_name ?? "—"}</td>
                      <td className={tdClass}>{issue.message}<span className="sr-only"> (rule {issue.code})</span></td>
                      <td className={`${tdClass} break-all`}>{issue.excerpt ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Disclosure>
        ) : null}
      </SectionCard>
    </div>
  );
}
