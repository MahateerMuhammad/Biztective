import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Pager, cursorParam } from "@/components/pager";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { getBusiness, pagePlans } from "@/lib/api/server";
import type { ActionStatus, PlanStatus } from "@/lib/api/types";

export const metadata: Metadata = { title: "Decision center" };

const PLAN: Record<PlanStatus, string> = {
  queued: "Preparing",
  running: "Preparing",
  awaiting_decision: "Waiting for a decision",
  approved: "Approved",
  rejected: "Rejected",
  failed: "Failed",
};
const EXECUTION: Record<ActionStatus, string> = {
  pending: "In progress",
  succeeded: "Written, checking",
  verified: "Verified in the sheet",
  failed: "Failed, nothing written",
  manual_review: "Needs review",
};

export default async function DecisionsPage({ params, searchParams }: PageProps<"/businesses/[businessId]/decisions">) {
  const { businessId } = await params;
  const cursor = cursorParam((await searchParams).cursor);
  const [business, page] = await Promise.all([getBusiness(businessId), pagePlans(businessId, cursor)]);
  if (!business) notFound();
  return (
    <div className="grid gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Decision center</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Recovery plans built from validated findings: compare strategies, decide, and track what was carried out.
        </p>
      </div>
      <Card>
        <CardContent className="overflow-x-auto pt-4">
          {page.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No recovery plans yet. A plan can be created from a completed investigation.{" "}
              <Link href={`/businesses/${businessId}/investigations`} className="font-medium underline-offset-4 hover:underline">
                See investigations
              </Link>
            </p>
          ) : (
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Recovery plans, newest first</caption>
              <thead>
                <tr className="border-b">
                  <th scope="col" className="py-2 font-medium">Created</th>
                  <th scope="col" className="py-2 font-medium">Decision</th>
                  <th scope="col" className="py-2 font-medium">Version</th>
                  <th scope="col" className="py-2 font-medium">Execution</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((p) => (
                  <tr key={p.id} className="border-b last:border-0">
                    <td className="py-2">
                      <Link href={`/businesses/${businessId}/recovery-plans/${p.id}`} className="font-medium underline-offset-4 hover:underline">
                        {p.created_at.slice(0, 16).replace("T", " ")}
                      </Link>
                    </td>
                    <td className="py-2">
                      <Badge className={p.status === "awaiting_decision" ? "border-human-foreground/30 bg-human text-human-foreground" : "bg-muted"}>
                        {PLAN[p.status]}
                      </Badge>
                    </td>
                    <td className="py-2 tabular-nums">
                      {p.current_version || "—"}
                      {p.approved_version ? ` (approved v${p.approved_version})` : ""}
                    </td>
                    <td className="py-2">{p.execution_status ? EXECUTION[p.execution_status] : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <Pager path={`/businesses/${businessId}/decisions`} cursor={cursor} next={page.next_cursor} />
        </CardContent>
      </Card>
    </div>
  );
}
