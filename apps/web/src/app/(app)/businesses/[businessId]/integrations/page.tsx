import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { ConnectSheet } from "@/components/integrations/connect-sheet";
import { PageHeader, SectionCard, StatusChip, tableClass, tableWrap, tdClass, thClass, trClass } from "@/components/page";
import { getBusiness, listIntegrations } from "@/lib/api/server";
import { formatDate } from "@/lib/format";

export const metadata: Metadata = { title: "Integrations" };

const STATUS: Record<string, { label: string; tone: "ok" | "warn" | "idle" }> = {
  pending_verification: { label: "Waiting for proof of ownership", tone: "warn" },
  verified: { label: "Connected", tone: "ok" },
  revoked: { label: "Disconnected", tone: "idle" },
};

export default async function IntegrationsPage({ params }: PageProps<"/businesses/[businessId]/integrations">) {
  const { businessId } = await params;
  const business = await getBusiness(businessId);
  if (!business || business.role === "viewer") notFound();
  const integrations = await listIntegrations(businessId);
  if (!integrations) notFound();
  const verified = integrations.items.find((i) => i.status === "verified") ?? null;
  const pending = integrations.items.find((i) => i.status === "pending_verification") ?? null;

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Integrations"
        subtitle="Connect the places where approved plans are carried out."
        back={
          <Link href={`/businesses/${businessId}`} className="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
            ← {business.name}
          </Link>
        }
      />
      <SectionCard
        title="Google Sheets"
        help="Approved recovery plans are written to one spreadsheet that you own."
        tip="Biztective can only reach spreadsheets you share with it, and never contacts customers."
      >
        <div className="grid gap-4 text-[15px]">
          {!integrations.available ? (
            <p>Google Sheets is not configured on this server.</p>
          ) : verified ? (
            <p className="flex flex-wrap items-center gap-2">
              <StatusChip tone="ok">Connected and verified</StatusChip>
              <a href={verified.spreadsheet_url} target="_blank" rel="noopener noreferrer" className="font-medium underline underline-offset-4">
                Open the spreadsheet<span className="sr-only"> (opens in a new tab)</span>
              </a>
              <span className="text-muted-foreground">verified {formatDate(verified.verified_at, true)}</span>
            </p>
          ) : business.role === "owner" ? (
            <ConnectSheet businessId={businessId} pending={pending} />
          ) : (
            <p>No spreadsheet is connected. Ask an owner to connect one.</p>
          )}
          {integrations.items.length > 1 ? (
            <div className={tableWrap} tabIndex={0} role="region" aria-label="Table, scrollable">
              <table className={tableClass}>
                <caption className="sr-only">Spreadsheet connections</caption>
                <thead>
                  <tr>
                    <th scope="col" className={thClass}>Spreadsheet</th>
                    <th scope="col" className={thClass}>Status</th>
                    <th scope="col" className={thClass}>Added</th>
                  </tr>
                </thead>
                <tbody>
                  {integrations.items.map((i) => {
                    const status = STATUS[i.status] ?? { label: i.status, tone: "idle" as const };
                    return (
                      <tr key={i.id} className={trClass}>
                        <td className={tdClass}>
                          <a href={i.spreadsheet_url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-4">
                            Open spreadsheet<span className="sr-only"> (opens in a new tab)</span>
                          </a>
                        </td>
                        <td className={tdClass}><StatusChip tone={status.tone}>{status.label}</StatusChip></td>
                        <td className={tdClass}>{formatDate(i.created_at, true)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      </SectionCard>
    </div>
  );
}
