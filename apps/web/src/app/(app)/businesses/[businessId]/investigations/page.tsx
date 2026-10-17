import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Pager, cursorParam } from "@/components/pager";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { getBusiness, pageInvestigations } from "@/lib/api/server";
import type { InvestigationStatus } from "@/lib/api/types";

export const metadata: Metadata = { title: "Investigations" };

const INVESTIGATION_STATUS: Record<InvestigationStatus, string> = {
  queued: "Queued",
  running: "Investigating",
  awaiting_human: "Needs your answer",
  completed: "Completed",
  completed_no_incidents: "No incident",
  insufficient_data: "Not enough data",
  failed: "Failed",
};

export default async function InvestigationsPage({ params, searchParams }: PageProps<"/businesses/[businessId]/investigations">) {
  const { businessId } = await params;
  const cursor = cursorParam((await searchParams).cursor);
  const [business, page] = await Promise.all([getBusiness(businessId), pageInvestigations(businessId, cursor)]);
  if (!business) notFound();
  return (
    <div className="grid gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Investigations</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Each investigation explains the incidents of one dataset version with evidence. Start one from Business health.
        </p>
      </div>
      <Card>
        <CardContent className="overflow-x-auto pt-4">
          {page.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No investigations yet.{" "}
              <Link href={`/businesses/${businessId}`} className="font-medium underline-offset-4 hover:underline">
                Go to Business health
              </Link>
            </p>
          ) : (
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Investigations, newest first</caption>
              <thead>
                <tr className="border-b">
                  <th scope="col" className="py-2 font-medium">Started</th>
                  <th scope="col" className="py-2 font-medium">Status</th>
                  <th scope="col" className="py-2 font-medium">Mode</th>
                  <th scope="col" className="py-2 font-medium">Completed</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((i) => (
                  <tr key={i.id} className="border-b last:border-0">
                    <td className="py-2">
                      <Link href={`/businesses/${businessId}/investigations/${i.id}`} className="font-medium underline-offset-4 hover:underline">
                        {i.created_at.slice(0, 16).replace("T", " ")}
                      </Link>
                    </td>
                    <td className="py-2">
                      <Badge className={i.status === "awaiting_human" ? "border-human-foreground/30 bg-human text-human-foreground" : "bg-muted"}>
                        {INVESTIGATION_STATUS[i.status]}
                      </Badge>
                    </td>
                    <td className="py-2">{i.llm_mode === "llm" ? "AI-assisted wording" : "Deterministic"}</td>
                    <td className="py-2">{i.completed_at ? i.completed_at.slice(0, 16).replace("T", " ") : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <Pager path={`/businesses/${businessId}/investigations`} cursor={cursor} next={page.next_cursor} />
        </CardContent>
      </Card>
    </div>
  );
}
