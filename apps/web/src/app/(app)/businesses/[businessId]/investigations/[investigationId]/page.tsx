import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/auto-refresh";
import { Progress } from "@/components/investigation/progress";
import { ProofChain } from "@/components/investigation/proof-chain";
import { QuestionForm } from "@/components/investigation/question-form";
import { KindBadge } from "@/components/kind-badge";
import { StartPlan } from "@/components/recovery/start-plan";
import { VersionDiff } from "@/components/investigation/version-diff";
import { Segments } from "@/components/segments";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { compareVersions, getBusiness, getInvestigation, listEvents, listPlans, listVersions } from "@/lib/api/server";
import type { InvestigationDetail, InvestigationIncident, InvestigationStatus } from "@/lib/api/types";
import { formatDateTime, templateLabel } from "@/lib/format";
import { planChip } from "@/lib/status";
import { PageHeader, StatusChip } from "@/components/page";
import { buttonVariants } from "@/components/ui/button";
import { InfoTip } from "@/components/ui/info-tip";

export const metadata: Metadata = { title: "Investigation" };

const WORKING: InvestigationStatus[] = ["queued", "running"];
const STATUS_TEXT: Record<InvestigationStatus, string> = {
  queued: "Queued…",
  running: "Biztective is investigating…",
  awaiting_human: "Waiting for your answer",
  completed: "Completed",
  completed_no_incidents: "Completed: no significant incident",
  insufficient_data: "Not enough history to investigate",
  failed: "Failed",
};

function Incident({ incident, evidenceBase }: { incident: InvestigationIncident; evidenceBase: string }) {
  return (
    <Card id={`incident-${incident.id}`} className="scroll-mt-4">
      <CardHeader>
        <CardTitle>
          <h3 className="flex flex-wrap items-center gap-2 text-base font-semibold">
            <span className="text-muted-foreground tabular-nums">#{incident.rank}</span>
            {incident.metric_label}
            {incident.value ? ` in ${incident.dimension} “${incident.value}”` : " across the business"}
            <StatusChip tone={incident.severity === "medium" ? "warn" : incident.severity === "low" ? "idle" : "err"}>{incident.severity.replace(/^./, (c) => c.toUpperCase())}</StatusChip>
          </h3>
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-5 text-[15px]">
        <ProofChain incident={incident} evidenceBase={evidenceBase} />
        {incident.human_context.length > 0 ? (
          <div className="grid gap-1">
            <h4 className="flex items-center gap-2 font-medium">
              Context you provided <KindBadge kind="human" />
            </h4>
            <ul className="grid gap-1">
              {incident.human_context.map((c) => (
                <li key={c.human_input_id}>
                  {c.answered_by.display_name ?? "A team member"} on {formatDateTime(c.answered_at)}:{" "}
                  {c.option_label ?? "comment only"}
                  {c.free_text ? (
                    <span className="block whitespace-pre-wrap text-muted-foreground">“{c.free_text}”</span>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {incident.recommendations.length > 0 ? (
          <div className="grid gap-1">
            <h4 className="font-medium">Recommended next checks</h4>
            <ul className="list-disc pl-5">
              {incident.recommendations.map((r) => (
                <li key={`${r.kind}-${r.text}`}>{r.text}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** What is still unknown, in one place: undecided explanations, missing data and caveats. */
function Uncertainties({ investigation }: { investigation: InvestigationDetail }) {
  const items: { key: string; text: string }[] = [];
  for (const incident of investigation.incidents.filter((i) => i.investigated)) {
    for (const h of incident.hypotheses) {
      if (h.status === "inconclusive" || h.status === "needs_human_context") {
        items.push({
          key: `${incident.id}-${h.id}`,
          text: `#${incident.rank}: “${templateLabel(h.template)}” is ${h.status === "inconclusive" ? "inconclusive — the data does not settle it" : "waiting for context only you can give"}.`,
        });
      }
    }
    for (const g of incident.data_gaps) {
      items.push({ key: `${incident.id}-${g.template}-${g.criterion}`, text: `#${incident.rank}: ${g.reason}` });
    }
  }
  for (const r of investigation.restrictions) items.push({ key: r.code, text: r.message });
  const caveats = investigation.findings?.rendered.caveats ?? [];
  if (items.length === 0 && caveats.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2 className="text-base font-semibold">Unresolved uncertainties</h2>
        </CardTitle>
        <CardDescription>Biztective does not guess. These points are open, and the conclusions above account for them.</CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="grid list-disc gap-1 pl-5 text-[15px]">
          {items.map((i) => (
            <li key={i.key}>{i.text}</li>
          ))}
          {caveats.map((c, i) => (
            <li key={`caveat-${i}`}>
              <Segments segments={c} />
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function Findings({ investigation }: { investigation: InvestigationDetail }) {
  const findings = investigation.findings;
  if (!findings) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2 className="text-base font-semibold">Findings (version {findings.version})</h2>
        </CardTitle>
        <CardDescription>{findings.label}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-2 text-[15px]">
        <p>
          <Segments segments={findings.rendered.summary} />
        </p>
        {findings.rendered.root_causes.map((c) => (
          <p key={c.hypothesis_id}>
            <span className="font-medium">Root cause: </span>
            <Segments segments={c.statement} />
          </p>
        ))}
        {(findings.rendered.explained_by_context ?? []).map((c) => (
          <p key={`${c.incident_id}-${c.hypothesis_id}`}>
            <span className="font-medium">Explained by your context: </span>
            <Segments segments={c.statement} />
          </p>
        ))}
        {(findings.rendered.conflicts ?? []).map((c) => (
          <p key={`${c.incident_id}-${c.hypothesis_id}`} role="note">
            <Segments segments={c.statement} />
          </p>
        ))}
        {findings.rendered.caveats.map((c, i) => (
          <p key={i} className="text-muted-foreground">
            <Segments segments={c} />
          </p>
        ))}
      </CardContent>
    </Card>
  );
}

export default async function InvestigationPage({
  params,
  searchParams,
}: PageProps<"/businesses/[businessId]/investigations/[investigationId]">) {
  const { businessId, investigationId } = await params;
  const query = await searchParams;
  const [business, investigation, versions, plans, events] = await Promise.all([
    getBusiness(businessId),
    getInvestigation(businessId, investigationId),
    listVersions(businessId, investigationId),
    listPlans(businessId, investigationId),
    listEvents(businessId, investigationId),
  ]);
  if (!business || !investigation) notFound();
  const latest = versions.at(-1)?.version ?? 0;
  const from = Math.min(Number(query.from ?? 1) || 1, latest);
  const to = Math.min(Number(query.to ?? latest) || latest, latest);
  const comparison = latest >= 2 && from !== to ? await compareVersions(businessId, investigationId, from, to) : null;
  const awaiting = investigation.awaiting;
  const working = WORKING.includes(investigation.status);
  const canAnswer = business.role !== "viewer";
  const investigated = investigation.incidents.filter((i) => i.investigated);
  const evidenceBase = `/businesses/${businessId}/evidence`;
  const openPlan = plans.find((p) => ["queued", "running", "awaiting_decision", "approved"].includes(p.status));

  return (
    <div className="grid gap-6">
      {working ? <AutoRefresh /> : null}
      <PageHeader
        back={
          <Link href={`/businesses/${businessId}/investigations`} className="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
            ← Investigations
          </Link>
        }
        title="Investigation"
        subtitle={
          <span className="inline-flex flex-wrap items-center gap-x-1">
            {STATUS_TEXT[investigation.status]}
            {investigation.version ? ` · version ${investigation.version}` : ""}
            <InfoTip label="How this was written">
              {investigation.llm_mode === "deterministic"
                ? "Written by fixed rules from the measured data."
                : "The wording is AI-assisted. The numbers and verdicts come from fixed rules, never from the AI."}
            </InfoTip>
            {investigation.status === "failed" ? <span className="text-destructive"> We couldn’t finish (reference {investigation.error_code}).</span> : null}
          </span>
        }
        action={
          <Link href={`/businesses/${businessId}/datasets/${investigation.dataset_id}/analytics`} className={buttonVariants({ variant: "outline" })}>
            View analytics
          </Link>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <div className="grid min-w-0 content-start gap-6">
          {awaiting ? (
            <Card className="border-human-foreground/40">
              <CardHeader>
                <CardTitle>
                  <h2 className="flex items-center gap-2 text-base font-semibold">
                    Biztective needs your input <KindBadge kind="human" />
                  </h2>
                </CardTitle>
                <CardDescription id={`why-${awaiting.id}`}>
                  {awaiting.why} About: {awaiting.claim}
                </CardDescription>
              </CardHeader>
              <CardContent>
                {canAnswer ? (
                  <QuestionForm businessId={businessId} investigationId={investigation.id} clarification={awaiting} />
                ) : (
                  <Alert>
                    <AlertTitle>{awaiting.question}</AlertTitle>
                    <AlertDescription>An owner or analyst of this business can answer.</AlertDescription>
                  </Alert>
                )}
              </CardContent>
            </Card>
          ) : null}

          <Findings investigation={investigation} />

          {investigation.status === "completed" ? (
            <Card>
              <CardHeader>
                <CardTitle>
                  <h2 className="flex items-center gap-2 text-base font-semibold">
                    Decide what to do <KindBadge kind="action" />
                  </h2>
                </CardTitle>
                <CardDescription>
                  Turn the validated findings into competing recovery strategies, set your limits, and approve or reject in the
                  Decision center. Nothing happens outside Biztective until an owner approves and starts it.
                </CardDescription>
              </CardHeader>
              <CardContent className="grid gap-2 text-[15px]">
                {plans.length > 0 ? (
                  <ul className="grid gap-1" aria-label="Recovery plans">
                    {plans.map((p) => (
                      <li key={p.id}>
                        <Link href={`/businesses/${businessId}/recovery-plans/${p.id}`} className="font-medium underline-offset-4 hover:underline">
                          Plan from {formatDateTime(p.created_at)}: {planChip(p.status).label}
                          {p.current_version ? ` (version ${p.current_version})` : ""}
                        </Link>
                      </li>
                    ))}
                  </ul>
                ) : null}
                {canAnswer && !openPlan ? <StartPlan businessId={businessId} investigationId={investigation.id} /> : null}
              </CardContent>
            </Card>
          ) : null}

          <section className="grid gap-4" aria-labelledby="incidents-heading">
            <h2 id="incidents-heading" className="text-lg font-semibold">
              What changed, and the proof
            </h2>
            {investigated.length === 0 ? (
              <p className="text-[15px] text-muted-foreground">{working ? "The agent is still working through the incidents…" : "No incident was investigated."}</p>
            ) : null}
            {investigated.map((incident) => (
              <Incident key={incident.id} incident={incident} evidenceBase={evidenceBase} />
            ))}
          </section>

          {latest >= 2 ? (
            <Card>
              <CardHeader>
                <CardTitle>
                  <h2 className="text-base font-semibold">Investigation replay</h2>
                </CardTitle>
                <CardDescription>Each answer created a new version. Compare what Biztective concluded before and after.</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4">
                <nav aria-label="Versions" className="flex flex-wrap gap-2 text-[15px]">
                  {versions.slice(1).map((v) => (
                    <Link
                      key={v.version}
                      href={`?from=${v.version - 1}&to=${v.version}`}
                      className="inline-flex min-h-11 items-center rounded-lg border px-3 underline-offset-4 hover:underline aria-[current=page]:bg-muted"
                      aria-current={from === v.version - 1 && to === v.version ? "page" : undefined}
                    >
                      v{v.version - 1} → v{v.version}
                    </Link>
                  ))}
                  <Link
                    href={`?from=1&to=${latest}`}
                    className="inline-flex min-h-11 items-center rounded-lg border px-3 underline-offset-4 hover:underline aria-[current=page]:bg-muted"
                    aria-current={from === 1 && to === latest ? "page" : undefined}
                  >
                    v1 → v{latest} (all answers)
                  </Link>
                </nav>
                {comparison ? (
                  <>
                    <h3 className="font-medium">
                      Version {comparison.from_version} → version {comparison.to_version}
                    </h3>
                    <VersionDiff comparison={comparison} />
                  </>
                ) : null}
              </CardContent>
            </Card>
          ) : null}
        </div>

        <aside className="grid content-start gap-6" aria-label="Investigation overview">
          <Card>
            <CardHeader>
              <CardTitle>
                <h2 className="text-base font-semibold">Agent progress</h2>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Progress events={events.items} working={working} />
            </CardContent>
          </Card>
          {investigated.length > 1 ? (
            <Card>
              <CardHeader>
                <CardTitle>
                  <h2 className="text-base font-semibold">Jump to a change</h2>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <nav aria-label="Jump to incident">
                  <ol className="grid gap-1 text-[15px]">
                    {investigated.map((i) => (
                      <li key={i.id}>
                        <a href={`#incident-${i.id}`} className="underline-offset-4 hover:underline">
                          #{i.rank} {i.metric_label}
                          {i.value ? ` · ${i.value}` : ""}
                        </a>
                      </li>
                    ))}
                  </ol>
                </nav>
              </CardContent>
            </Card>
          ) : null}
          <Uncertainties investigation={investigation} />
        </aside>
      </div>
    </div>
  );
}
