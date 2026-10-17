import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { SearchCheck } from "lucide-react";

import { EmptyState, PageHeader, SectionCard, StatusChip, rowLinkClass, tableClass, tableWrap, tdClass, thClass, trClass } from "@/components/page";
import { Pager, cursorParam } from "@/components/pager";
import { buttonVariants } from "@/components/ui/button";
import { getBusiness, pageInvestigations } from "@/lib/api/server";
import { formatDateTime } from "@/lib/format";
import { investigationChip } from "@/lib/status";

export const metadata: Metadata = { title: "Investigations" };

export default async function InvestigationsPage({ params, searchParams }: PageProps<"/businesses/[businessId]/investigations">) {
  const { businessId } = await params;
  const cursor = cursorParam((await searchParams).cursor);
  const [business, page] = await Promise.all([getBusiness(businessId), pageInvestigations(businessId, cursor)]);
  if (!business) notFound();
  return (
    <div className="grid gap-6">
      <PageHeader title="Investigations" subtitle="Each one explains the biggest changes in one version of your data." />
      <SectionCard>
        {page.items.length === 0 ? (
          <EmptyState
            icon={SearchCheck}
            title="No investigations yet"
            action={<Link href={`/businesses/${businessId}`} className={buttonVariants()}>Go to Business health</Link>}
          >
            Start one from Business health, next to a change you want explained.
          </EmptyState>
        ) : (
          <div className={tableWrap} tabIndex={0} role="region" aria-label="Table, scrollable">
            <table className={tableClass}>
              <caption className="sr-only">Investigations, newest first</caption>
              <thead>
                <tr>
                  <th scope="col" className={thClass}>Started</th>
                  <th scope="col" className={thClass}>Status</th>
                  <th scope="col" className={thClass}>Finished</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((i) => {
                  const chip = investigationChip(i.status);
                  return (
                    <tr key={i.id} className={trClass}>
                      <td className={tdClass}>
                        <Link href={`/businesses/${businessId}/investigations/${i.id}`} className={rowLinkClass}>
                          {formatDateTime(i.created_at)}
                        </Link>
                      </td>
                      <td className={tdClass}><StatusChip tone={chip.tone}>{chip.label}</StatusChip></td>
                      <td className={`${tdClass} whitespace-nowrap`}>{i.completed_at ? formatDateTime(i.completed_at) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <Pager path={`/businesses/${businessId}/investigations`} cursor={cursor} next={page.next_cursor} />
      </SectionCard>
    </div>
  );
}
