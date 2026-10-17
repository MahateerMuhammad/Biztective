import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { PageHeader, SectionCard, StatusChip } from "@/components/page";
import { ConnectForm, SyncForm } from "@/components/sources/connect-form";
import { getBusiness, listSources } from "@/lib/api/server";
import type { SourceConnection } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";

export const metadata: Metadata = { title: "Connect a database" };

function syncStatus(c: SourceConnection): { label: string; tone: "idle" | "ok" | "err"; text: string } {
  if (c.last_sync_status === "failed") {
    return { label: "Failed", tone: "err", text: c.last_sync_error ?? "The last sync did not finish." };
  }
  if (c.last_sync_status === "succeeded") {
    return {
      label: "Synchronized",
      tone: "ok",
      text: c.last_sync_at ? `Data copied ${formatDateTime(c.last_sync_at)}.` : "Data copied.",
    };
  }
  if (c.last_dataset_id) return { label: "Syncing", tone: "idle", text: "Data is being copied and checked." };
  return { label: "Not synced yet", tone: "idle", text: "The connection is valid, but no data has been copied yet." };
}

export default async function ConnectSourcePage({ params }: PageProps<"/businesses/[businessId]/datasets/connect">) {
  const { businessId } = await params;
  const [business, sources] = await Promise.all([getBusiness(businessId), listSources(businessId)]);
  if (!business) notFound();
  const canConnect = business.role === "owner" || business.role === "analyst";
  const items = sources ?? [];

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Connect a PostgreSQL database"
        subtitle="Each sync copies your tables into a new, checked version of your data."
        back={
          <Link href={`/businesses/${businessId}/datasets`} className="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
            ← Data
          </Link>
        }
      />
      {canConnect ? (
        <SectionCard
          title="New connection"
          help="Testing only checks that Biztective can sign in and list tables. Nothing is copied until you sync."
        >
          <ConnectForm businessId={businessId} />
        </SectionCard>
      ) : null}
      <SectionCard title="Saved connections" help="Password is not stored — enter it to sync.">
        {items.length === 0 ? (
          <p className="text-[15px] text-muted-foreground">No saved connections yet.</p>
        ) : (
          <ul className="grid gap-5">
            {items.map((c) => {
              const status = syncStatus(c);
              return (
                <li key={c.id} className="grid gap-3 border-t border-border pt-4 first:border-t-0 first:pt-0">
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="font-medium">
                      {c.database} on {c.host}:{c.port}
                    </span>
                    <StatusChip tone={status.tone}>{status.label}</StatusChip>
                  </div>
                  <p className="text-[13px] text-muted-foreground">
                    {status.text} Tables: {c.schema_name}.{c.table_orders}, {c.table_products}, {c.table_customers}, {c.table_returns}.
                    {c.last_dataset_id ? (
                      <>
                        {" "}
                        <Link className="underline underline-offset-4" href={`/businesses/${businessId}/datasets/${c.last_dataset_id}`}>
                          View latest version
                        </Link>
                      </>
                    ) : null}
                  </p>
                  {canConnect ? <SyncForm businessId={businessId} connection={c} /> : null}
                </li>
              );
            })}
          </ul>
        )}
      </SectionCard>
    </div>
  );
}
