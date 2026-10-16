import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/auto-refresh";
import { QuestionForm } from "@/components/investigation/question-form";
import { VersionDiff } from "@/components/investigation/version-diff";
import { Segments } from "@/components/segments";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { compareVersions, getBusiness, getInvestigation, listVersions } from "@/lib/api/server";
import type { InvestigationDetail, InvestigationIncident, InvestigationStatus } from "@/lib/api/types";
import { STATUS_LABELS, templateLabel } from "@/lib/format";

export const metadata: Metadata = { title: "Investigation" };

const WORKING: InvestigationStatus[] = ["queued", "running"];
const STATUS_TEXT: Record<InvestigationStatus, string> = {
  queued: "Queued…",
  running: "ROOT is investigating…",
  awaiting_human: "Waiting for your answer",
  completed: "Completed",
  completed_no_incidents: "Completed: no significant incident",
  insufficient_data: "Not enough history to investigate",
  failed: "Failed",
};
const INTERPRETATION = {
  intended: "Deliberate business decision",
  unintended: "Confirmed as unplanned",
} as const;

function percent(value: string | null): string {
  return value === null ? "—" : `${(Number(value) * 100).toFixed(0)}%`;
}

function Incident({ incident }: { incident: InvestigationIncident }) {
  const root = incident.hypotheses.find((h) => h.id === incident.root_cause_hypothesis_id);
  const explained = incident.hypotheses.filter((h) => incident.explained_by_context.includes(h.id));
  const conflicted = incident.hypotheses.filter((h) => h.conflicts.length > 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h3 className="text-base font-semibold">
            #{incident.rank} · {incident.metric_label}
            {incident.value ? ` in ${incident.dimension} “${incident.value}”` : ""}
          </h3>
        </CardTitle>
        <CardDescription className="uppercase">{incident.severity}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 text-sm">
        <ul className="grid gap-1" aria-label="Measured facts">
          {incident.observations.map((o, i) => (
            <li key={i}>
              <Segments segments={o} />
            </li>
          ))}
        </ul>
        <div className="grid gap-1">
          <p>
            <span className="font-medium">Root cause: </span>
            {root ? templateLabel(root.template) : "No unplanned cause is supported"}
          </p>
          {explained.map((h) => (
            <p key={h.id}>
              <span className="font-medium">Explained by your context: </span>
              {templateLabel(h.template)} ({INTERPRETATION.intended.toLowerCase()}; its measured
              impact is unchanged)
            </p>
          ))}
          {conflicted.map((h) => (
            <p key={h.id} role="note">
              <span className="font-medium">Conflict: </span>
              an answer about {templateLabel(h.template).toLowerCase()} contradicts the measured
              data. The measurements were kept.
            </p>
          ))}
        </div>
        <table className="w-full text-left">
          <caption className="sr-only">Competing explanations and their deterministic verdicts</caption>
          <thead>
            <tr className="border-b">
              <th scope="col" className="py-1 font-medium">Explanation</th>
              <th scope="col" className="py-1 font-medium">Status</th>
              <th scope="col" className="py-1 text-right font-medium">Confidence</th>
              <th scope="col" className="py-1 font-medium">Interpretation</th>
            </tr>
          </thead>
          <tbody>
            {incident.hypotheses.map((h) => (
              <tr key={h.id} className="border-b last:border-0">
                <th scope="row" className="py-1 font-normal">
                  {templateLabel(h.template)}
                  {h.source === "human_context" ? (
                    <span className="block text-xs text-muted-foreground">added after your answer</span>
                  ) : null}
                </th>
                <td className="py-1">{STATUS_LABELS[h.status] ?? h.status}</td>
                <td className="py-1 text-right tabular-nums">{percent(h.confidence)}</td>
                <td className="py-1">{h.interpretation ? INTERPRETATION[h.interpretation] : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {incident.human_context.length > 0 ? (
          <div className="grid gap-1">
            <h4 className="font-medium">Context you provided</h4>
            <ul className="grid gap-1">
              {incident.human_context.map((c) => (
                <li key={c.human_input_id}>
                  {c.answered_by.display_name ?? "A team member"} on {c.answered_at.slice(0, 16).replace("T", " ")}:{" "}
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
      <CardContent className="grid gap-2 text-sm">
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
  const [business, investigation, versions] = await Promise.all([
    getBusiness(businessId),
    getInvestigation(businessId, investigationId),
    listVersions(businessId, investigationId),
  ]);
  if (!business || !investigation) notFound();
  const latest = versions.at(-1)?.version ?? 0;
  const from = Math.min(Number(query.from ?? 1) || 1, latest);
  const to = Math.min(Number(query.to ?? latest) || latest, latest);
  const comparison = latest >= 2 && from !== to ? await compareVersions(businessId, investigationId, from, to) : null;
  const awaiting = investigation.awaiting;
  const working = WORKING.includes(investigation.status);
  const canAnswer = business.role !== "viewer";

  return (
    <div className="grid gap-6">
      {working ? <AutoRefresh /> : null}
      <div>
        <Link href={`/businesses/${businessId}/datasets/${investigation.dataset_id}/analytics`} className="text-sm underline-offset-4 hover:underline">
          ← Analytics
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">Investigation workspace</h1>
        <p className="mt-1 text-sm" role="status" aria-live="polite">
          {STATUS_TEXT[investigation.status]}
          {investigation.version ? ` · version ${investigation.version}` : ""}
          {investigation.llm_mode === "deterministic" ? " · deterministic mode" : " · AI-assisted wording"}
        </p>
        {investigation.status === "failed" ? (
          <p className="text-sm text-muted-foreground">Error: {investigation.error_code}</p>
        ) : null}
      </div>

      {awaiting ? (
        <Card>
          <CardHeader>
            <CardTitle>
              <h2 className="text-base font-semibold">ROOT needs your input</h2>
            </CardTitle>
            <CardDescription id={`why-${awaiting.id}`}>
              {awaiting.why} About: {awaiting.claim}{" "}
              <span className="font-mono text-xs">({awaiting.incident_key})</span>
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

      <section className="grid gap-4" aria-labelledby="incidents-heading">
        <h2 id="incidents-heading" className="text-lg font-semibold">
          Incidents
        </h2>
        {investigation.incidents
          .filter((i) => i.investigated)
          .map((incident) => (
            <Incident key={incident.id} incident={incident} />
          ))}
      </section>

      {latest >= 2 ? (
        <Card>
          <CardHeader>
            <CardTitle>
              <h2 className="text-base font-semibold">Investigation replay</h2>
            </CardTitle>
            <CardDescription>
              Each answer created a new version. Compare what ROOT concluded before and after.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <nav aria-label="Versions" className="flex flex-wrap gap-2 text-sm">
              {versions.slice(1).map((v) => (
                <Link
                  key={v.version}
                  href={`?from=${v.version - 1}&to=${v.version}`}
                  className="rounded-md border px-2 py-1 underline-offset-4 hover:underline"
                  aria-current={from === v.version - 1 && to === v.version ? "page" : undefined}
                >
                  v{v.version - 1} → v{v.version}
                </Link>
              ))}
              <Link
                href={`?from=1&to=${latest}`}
                className="rounded-md border px-2 py-1 underline-offset-4 hover:underline"
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
  );
}
