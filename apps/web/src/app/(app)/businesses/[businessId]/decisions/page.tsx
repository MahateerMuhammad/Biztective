import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ClipboardList } from "lucide-react";

import { EmptyState, PageHeader, SectionCard, StatusChip, rowLinkClass, tableClass, tableWrap, tdClass, thClass, trClass } from "@/components/page";
import { Pager, cursorParam } from "@/components/pager";
import { buttonVariants } from "@/components/ui/button";
import { getBusiness, pagePlans } from "@/lib/api/server";
import { formatDateTime } from "@/lib/format";
import { executionLabel, planChip } from "@/lib/status";

export const metadata: Metadata = { title: "Decision center" };

export default async function DecisionsPage({ params, searchParams }: PageProps<"/businesses/[businessId]/decisions">) {
  const { businessId } = await params;
  const cursor = cursorParam((await searchParams).cursor);
  const [business, page] = await Promise.all([getBusiness(businessId), pagePlans(businessId, cursor)]);
  if (!business) notFound();
  return (
    <div className="grid gap-6">
      <PageHeader title="Decision center" subtitle="Compare ways to recover, decide, and track what was carried out." />
      <SectionCard>
        {page.items.length === 0 ? (
          <EmptyState
            icon={ClipboardList}
            title="No recovery plans yet"
            action={<Link href={`/businesses/${businessId}/investigations`} className={buttonVariants()}>See investigations</Link>}
          >
            A plan can be created from a completed investigation.
          </EmptyState>
        ) : (
          <div className={tableWrap} tabIndex={0} role="region" aria-label="Table, scrollable">
            <table className={tableClass}>
              <caption className="sr-only">Recovery plans, newest first</caption>
              <thead>
                <tr>
                  <th scope="col" className={thClass}>Created</th>
                  <th scope="col" className={thClass}>Decision</th>
                  <th scope="col" className={thClass}>Progress</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((p) => {
                  const chip = planChip(p.status);
                  return (
                    <tr key={p.id} className={trClass}>
                      <td className={tdClass}>
                        <Link href={`/businesses/${businessId}/recovery-plans/${p.id}`} className={rowLinkClass}>
                          {formatDateTime(p.created_at)}
                        </Link>
                      </td>
                      <td className={tdClass}>
                        <StatusChip tone={chip.tone}>{chip.label}</StatusChip>
                        {p.approved_version ? <span className="ml-2 text-muted-foreground">Version {p.approved_version} approved</span> : null}
                      </td>
                      <td className={tdClass}>{p.execution_status ? executionLabel(p.execution_status) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <Pager path={`/businesses/${businessId}/decisions`} cursor={cursor} next={page.next_cursor} />
      </SectionCard>
    </div>
  );
}
