import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { ConnectSheet } from "@/components/integrations/connect-sheet";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getBusiness, listIntegrations } from "@/lib/api/server";

export const metadata: Metadata = { title: "Integrations" };

const STATUS: Record<string, string> = {
  pending_verification: "Waiting for proof of ownership",
  verified: "Connected and verified",
  revoked: "Disconnected",
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
      <div>
        <Link href={`/businesses/${businessId}`} className="text-sm underline-offset-4 hover:underline">
          ← {business.name}
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">Integrations</h1>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>
            <h2 className="text-base font-semibold">Google Sheets</h2>
          </CardTitle>
          <CardDescription>
            Approved recovery plans are written to one spreadsheet that you own. ROOT can only reach spreadsheets you share
            with it, and never contacts customers.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 text-sm">
          {!integrations.available ? (
            <p>Google Sheets is not configured on this server.</p>
          ) : verified ? (
            <p>
              {STATUS.verified}:{" "}
              <a href={verified.spreadsheet_url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-4">
                open the spreadsheet
              </a>{" "}
              (verified {verified.verified_at?.slice(0, 10)}).
            </p>
          ) : business.role === "owner" ? (
            <ConnectSheet businessId={businessId} pending={pending} />
          ) : (
            <p>No spreadsheet is connected. Ask an owner to connect one.</p>
          )}
          {integrations.items.length > 0 ? (
            <table className="w-full text-left">
              <caption className="sr-only">Spreadsheet connections</caption>
              <thead>
                <tr>
                  <th scope="col">Spreadsheet</th>
                  <th scope="col">Status</th>
                  <th scope="col">Created</th>
                </tr>
              </thead>
              <tbody>
                {integrations.items.map((i) => (
                  <tr key={i.id}>
                    <td className="font-mono text-xs">{i.spreadsheet_id}</td>
                    <td>{STATUS[i.status]}</td>
                    <td>{i.created_at.slice(0, 10)}</td>
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
