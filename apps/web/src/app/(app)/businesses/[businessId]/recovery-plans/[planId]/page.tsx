import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/auto-refresh";
import { DecisionPanel } from "@/components/recovery/decision-panel";
import { StrategyCard } from "@/components/recovery/strategy-card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getBusiness, getPlan } from "@/lib/api/server";
import type { PlanDecisionView, PlanStatus } from "@/lib/api/types";
import { PLAN_ISSUES, formatMoney } from "@/lib/format";

export const metadata: Metadata = { title: "Recovery plan" };

const STATUS_TEXT: Record<PlanStatus, string> = {
  queued: "Preparing…",
  running: "ROOT is preparing the plan…",
  awaiting_decision: "Waiting for your decision",
  approved: "Approved",
  rejected: "Rejected",
  failed: "Failed",
};
const DECISION_TEXT: Record<PlanDecisionView["decision"], string> = {
  approve: "approved",
  reject: "rejected",
  request_changes: "requested changes to",
  revise: "edited the approved",
};

export default async function PlanPage({ params }: PageProps<"/businesses/[businessId]/recovery-plans/[planId]">) {
  const { businessId, planId } = await params;
  const [business, plan] = await Promise.all([getBusiness(businessId), getPlan(businessId, planId)]);
  if (!business || !plan) notFound();
  const current = plan.current;
  const content = current?.content;
  const working = plan.status === "queued" || plan.status === "running";

  return (
    <div className="grid gap-6">
      {working ? <AutoRefresh /> : null}
      <div>
        <Link href={`/businesses/${businessId}/investigations/${plan.investigation_id}`} className="text-sm underline-offset-4 hover:underline">
          ← Investigation
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">Recovery plan</h1>
        <p className="mt-1 text-sm" role="status" aria-live="polite">
          {STATUS_TEXT[plan.status]}
          {current ? ` · version ${current.version}` : ""}
          {current ? ` · content hash ${current.content_hash.slice(0, 12)}…` : ""}
        </p>
        {plan.status === "failed" ? <p className="text-sm text-muted-foreground">Error: {plan.error_code}</p> : null}
      </div>

      {plan.approved ? (
        <Alert>
          <AlertTitle>
            Approved version {plan.approved.version} by {plan.approved.approved_by.display_name ?? "an owner"}
          </AlertTitle>
          <AlertDescription>
            Content hash <span className="font-mono">{plan.approved.content_hash.slice(0, 12)}…</span>. Approval records your
            decision; nothing has been executed{plan.execution.available ? "" : " (execution is not available yet)"}. Editing the
            plan removes this approval.
          </AlertDescription>
        </Alert>
      ) : null}

      {current && content ? (
        <>
          <Card>
            <CardHeader>
              <CardTitle>
                <h2 className="text-base font-semibold">Your constraints and decision</h2>
              </CardTitle>
              <CardDescription>
                Budget {formatMoney(content.constraints.budget_amount, content.currency)} · horizon{" "}
                {content.constraints.horizon_weeks} weeks · maximum discount{" "}
                {(Number(content.constraints.max_discount_pct) * 100).toFixed(1)}% · permitted actions:{" "}
                {content.constraints.allowed_action_types.join(", ")}
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 text-sm">
              {content.totals ? (
                <p>
                  Selected strategies, estimated net over the horizon: {formatMoney(content.totals.scenarios.low.net, content.currency)} (low) ·{" "}
                  {formatMoney(content.totals.scenarios.mid.net, content.currency)} (mid) ·{" "}
                  {formatMoney(content.totals.scenarios.high.net, content.currency)} (high). These are estimates, not results.
                </p>
              ) : null}
              {current.validation.errors.length > 0 ? (
                <Alert variant="destructive">
                  <AlertTitle>This version cannot be approved yet</AlertTitle>
                  <AlertDescription>
                    <ul className="list-disc pl-5" aria-label="Problems to fix">
                      {current.validation.errors.map((e, i) => (
                        <li key={i}>
                          {PLAN_ISSUES[e.code] ?? e.code}
                          {e.field ? ` (${e.field.replaceAll("_", " ")})` : ""}
                        </li>
                      ))}
                    </ul>
                  </AlertDescription>
                </Alert>
              ) : null}
              {current.validation.warnings.length > 0 ? (
                <ul className="list-disc pl-5 text-muted-foreground" aria-label="Warnings">
                  {current.validation.warnings.map((w, i) => (
                    <li key={i}>{PLAN_ISSUES[w.code] ?? w.code}</li>
                  ))}
                </ul>
              ) : null}
              <DecisionPanel businessId={businessId} plan={plan} role={business.role} />
            </CardContent>
          </Card>

          <section className="grid gap-4" aria-labelledby="strategies-heading">
            <h2 id="strategies-heading" className="text-lg font-semibold">
              Competing strategies
            </h2>
            {content.strategies.map((s) => (
              <StrategyCard
                key={s.id}
                strategy={s}
                currency={content.currency}
                selected={content.selected.includes(s.id)}
                actionLabels={plan.catalog.action_types}
              />
            ))}
          </section>
        </>
      ) : null}

      {plan.decisions.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>
              <h2 className="text-base font-semibold">Decision history</h2>
            </CardTitle>
            <CardDescription>Every decision is kept, in order, and cannot be changed.</CardDescription>
          </CardHeader>
          <CardContent>
            <ol className="grid gap-2 text-sm" aria-label="Decisions">
              {plan.decisions.map((d) => (
                <li key={d.id}>
                  {d.decided_by.display_name ?? "A team member"} ({d.decided_role}) {DECISION_TEXT[d.decision]} version {d.version}{" "}
                  <span className="font-mono text-xs">({d.content_hash.slice(0, 12)}…)</span> on{" "}
                  {d.created_at.slice(0, 16).replace("T", " ")}
                  {d.outcome === "refused" ? ` — not applied: ${d.refusal?.replaceAll("_", " ")}` : ""}
                  {d.reason ? <span className="block whitespace-pre-wrap text-muted-foreground">“{d.reason}”</span> : null}
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
