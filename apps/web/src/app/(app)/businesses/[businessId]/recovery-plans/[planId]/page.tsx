import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/auto-refresh";
import { DecisionPanel } from "@/components/recovery/decision-panel";
import { ExecutionHistory } from "@/components/recovery/execution-history";
import { ExecutionPanel } from "@/components/recovery/execution-panel";
import { StrategyCard } from "@/components/recovery/strategy-card";
import { StrategyComparison } from "@/components/recovery/strategy-comparison";
import { KindBadge } from "@/components/kind-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getAction, getBusiness, getPlan } from "@/lib/api/server";
import type { ActionDetail, PlanDecisionView, PlanStatus } from "@/lib/api/types";
import { PLAN_ISSUES, formatDateTime, formatMoney } from "@/lib/format";
import { PageHeader, StatusChip } from "@/components/page";
import { InfoTip } from "@/components/ui/info-tip";
import { planChip } from "@/lib/status";

export const metadata: Metadata = { title: "Decision center" };

const STATUS_TEXT: Record<PlanStatus, string> = {
  queued: "Preparing…",
  running: "Biztective is preparing the plan…",
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
  const actions = (await Promise.all(plan.execution.actions.map((a) => getAction(businessId, a.id)))).filter(
    (a): a is ActionDetail => a !== null,
  );
  const latest = actions.find((a) => a.plan_version === plan.approved?.version) ?? null;
  const executing = actions.some((a) => a.status === "pending" || a.status === "succeeded");
  const working = plan.status === "queued" || plan.status === "running" || executing;

  return (
    <div className="grid gap-6">
      {working ? <AutoRefresh /> : null}
      <PageHeader
        back={
          <nav aria-label="Breadcrumb" className="flex flex-wrap gap-x-4 gap-y-1">
            <Link href={`/businesses/${businessId}/decisions`} className="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
              ← Decision center
            </Link>
            <Link href={`/businesses/${businessId}/investigations/${plan.investigation_id}`} className="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
              Source investigation
            </Link>
          </nav>
        }
        title={
          <span className="flex flex-wrap items-center gap-3">
            Recovery plan
            <StatusChip tone={planChip(plan.status).tone}>{planChip(plan.status).label}</StatusChip>
          </span>
        }
        subtitle={
          <span className="inline-flex flex-wrap items-center gap-x-1">
            {STATUS_TEXT[plan.status]}
            {current ? ` · version ${current.version}` : ""}
            {current ? (
              <InfoTip label="Technical details">Content fingerprint {current.content_hash.slice(0, 12)}…</InfoTip>
            ) : null}
            {plan.status === "failed" ? <span className="text-destructive"> We couldn’t prepare it (reference {plan.error_code}).</span> : null}
          </span>
        }
      />

      {plan.approved ? (
        <Alert>
          <AlertTitle>
            Approved version {plan.approved.version} by {plan.approved.approved_by.display_name ?? "an owner"}
          </AlertTitle>
          <AlertDescription>
            Approval records your decision. Biztective executes nothing until an owner starts it below. Editing the plan removes this approval.
            <span className="sr-only"> Content fingerprint {plan.approved.content_hash.slice(0, 12)}.</span>
          </AlertDescription>
        </Alert>
      ) : null}

      {plan.approved || actions.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>
              <h2 className="text-base font-semibold">Execution</h2>
            </CardTitle>
            <CardDescription>
              Approved review tasks and customer-service queues are written to your own Google Sheet, then read back and
              checked. Every attempt is recorded.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <ExecutionPanel
              businessId={businessId}
              planId={plan.id}
              approved={plan.approved ? { version: plan.approved.version, content_hash: plan.approved.content_hash } : null}
              integrationReady={plan.execution.integration_ready}
              current={latest}
              role={business.role}
            />
            <ExecutionHistory actions={actions} />
          </CardContent>
        </Card>
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
            <CardContent className="grid gap-4 text-[15px]">
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

          <Card>
            <CardHeader>
              <CardTitle>
                <h2 className="flex items-center gap-2 text-base font-semibold">
                  Cost and trade-off comparison <KindBadge kind="estimate" />
                </h2>
              </CardTitle>
              <CardDescription>
                Over {content.constraints.horizon_weeks} weeks. Change assumptions, budget or selection with “Request changes”:
                every change creates a new version that is recalculated and needs its own approval.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <StrategyComparison content={content} />
            </CardContent>
          </Card>

          <section className="grid gap-4" aria-labelledby="strategies-heading">
            <h2 id="strategies-heading" className="text-lg font-semibold">
              Strategy details and evidence
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
            <ol className="grid gap-3 text-[15px]" aria-label="Decisions">
              {plan.decisions.map((d) => (
                <li key={d.id}>
                  {d.decided_by.display_name ?? "A team member"} ({d.decided_role}) {DECISION_TEXT[d.decision]} version {d.version}{" "}
                  on <span title={d.content_hash}>{formatDateTime(d.created_at)}</span>
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
