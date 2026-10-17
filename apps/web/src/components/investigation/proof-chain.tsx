import Link from "next/link";

import { KindBadge } from "@/components/kind-badge";
import { Segments } from "@/components/segments";
import { Badge } from "@/components/ui/badge";
import type { CriterionView, HypothesisView, InvestigationIncident, Segment } from "@/lib/api/types";
import { STATUS_LABELS, templateLabel } from "@/lib/format";

const VERDICT: Record<HypothesisView["status"], string> = {
  supported: "border-action-foreground/30 bg-action text-action-foreground",
  refuted: "bg-muted text-foreground line-through decoration-1",
  inconclusive: "border-hypothesis-foreground/30 bg-hypothesis text-hypothesis-foreground",
  needs_human_context: "border-human-foreground/30 bg-human text-human-foreground",
  pending: "bg-muted text-foreground",
};
const EVIDENCE: Record<string, string> = {
  incident_detection: "Detection statistics",
  source_records: "Source records",
  return_reason_mix: "Return reasons",
  metric_breakdown: "Breakdown",
  period_comparison: "Period comparison",
  return_investigation: "Return analysis",
  segment_analysis: "Segment analysis",
  customer_exposure: "Customer exposure",
  data_quality_check: "Data quality check",
  margin_bridge: "Margin bridge",
  human_context: "Your answer",
  slice_economics: "Slice economics",
};

/** Who asked for each piece of evidence; the AI can only choose among read-only checks. */
const SOURCE: Record<string, string> = { detection: "from detection", system: "by rule", llm: "chosen by AI" };

function isSegments(value: unknown): value is Segment[] {
  return Array.isArray(value) && value.every((s) => typeof s === "object" && s !== null && ("text" in s || "value" in s || "entity" in s));
}

function Criterion({ c, evidenceBase }: { c: CriterionView; evidenceBase: string }) {
  const mark = c.satisfied === true ? "✓" : c.satisfied === false ? "✗" : "?";
  const meaning = c.satisfied === true ? "met" : c.satisfied === false ? "not met" : "not determined";
  return (
    <li className="flex gap-2">
      <span aria-hidden="true" className="w-4 shrink-0 text-center font-semibold">
        {mark}
      </span>
      <span className="grid gap-0.5">
        <span>
          <span className="sr-only">Criterion {meaning}: </span>
          {c.description}
          {c.source === "human" ? (
            <>
              {" "}
              <KindBadge kind="human" />
            </>
          ) : null}
        </span>
        {(c.basis as unknown[]).filter(isSegments).map((segments, i) => (
          <span key={i} className="text-[13px] text-muted-foreground">
            <Segments segments={segments} evidenceBase={evidenceBase} />
          </span>
        ))}
      </span>
    </li>
  );
}

/** One incident as a chain: what changed → what was tested → evidence → conclusion. */
export function ProofChain({ incident, evidenceBase }: { incident: InvestigationIncident; evidenceBase: string }) {
  const root = incident.hypotheses.find((h) => h.id === incident.root_cause_hypothesis_id);
  const explained = incident.hypotheses.filter((h) => incident.explained_by_context.includes(h.id));
  const ordered = [...incident.hypotheses].sort((a, b) => Number(b.confidence) - Number(a.confidence));
  return (
    <ol className="grid gap-5 border-l-2 border-border pl-5" aria-label="Proof chain">
      <li className="relative grid gap-2">
        <span aria-hidden="true" className="absolute top-1 -left-[27px] size-3 rounded-full border-2 border-background bg-fact-foreground" />
        <h4 className="flex items-center gap-2 text-base font-semibold">
          1. What changed <KindBadge kind="fact" />
        </h4>
        <ul className="grid gap-1 text-sm" aria-label="Measured facts">
          {incident.observations.map((o, i) => (
            <li key={i}>
              <Segments segments={o} evidenceBase={evidenceBase} />
            </li>
          ))}
        </ul>
      </li>

      <li className="relative grid gap-2">
        <span aria-hidden="true" className="absolute top-1 -left-[27px] size-3 rounded-full border-2 border-background bg-hypothesis-foreground" />
        <h4 className="flex items-center gap-2 text-base font-semibold">
          2. Competing explanations <KindBadge kind="hypothesis" />
        </h4>
        <p className="text-[13px] text-muted-foreground">
          Each explanation is scored by fixed rules against the evidence. The AI may word the findings; it never decides a verdict.
        </p>
        <ul className="grid gap-3">
          {ordered.map((h) => (
            <li key={h.id} className="rounded-lg border p-3 text-sm">
              <details open={h.id === root?.id}>
                <summary className="flex cursor-pointer flex-wrap items-center gap-2">
                  <span className="font-medium">{templateLabel(h.template)}</span>
                  <Badge className={VERDICT[h.status]}>{STATUS_LABELS[h.status] ?? h.status}</Badge>
                  <span className="text-[13px] text-muted-foreground tabular-nums">
                    confidence {Math.round(Number(h.confidence) * 100)}%
                  </span>
                  {h.source === "human_context" ? <span className="text-[13px] text-muted-foreground">added after your answer</span> : null}
                  {h.interpretation ? (
                    <span className="flex items-center gap-1 text-[13px]">
                      <KindBadge kind="human" />
                      {h.interpretation === "intended" ? "Deliberate business decision" : "Confirmed as unplanned"}
                    </span>
                  ) : null}
                </summary>
                <ul className="mt-2 grid gap-1.5" aria-label={`Criteria for ${templateLabel(h.template)}`}>
                  {h.criteria.map((c) => (
                    <Criterion key={c.key} c={c} evidenceBase={evidenceBase} />
                  ))}
                </ul>
                {h.conflicts.length > 0 ? (
                  <p role="note" className="mt-2 text-[13px] text-risk-foreground">
                    An answer contradicts the measured data here; the measurements were kept.
                  </p>
                ) : null}
              </details>
            </li>
          ))}
        </ul>
      </li>

      <li className="relative grid gap-2">
        <span aria-hidden="true" className="absolute top-1 -left-[27px] size-3 rounded-full border-2 border-background bg-fact-foreground" />
        <h4 className="flex items-center gap-2 text-base font-semibold">
          3. Evidence trail <KindBadge kind="fact" />
        </h4>
        <ul className="flex flex-wrap gap-2 text-[13px]" aria-label="Evidence items">
          {incident.evidence.map((e) => (
            <li key={e.id}>
              <Link
                href={`${evidenceBase}/${e.id}`}
                className="inline-flex min-h-9 items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
              >
                {EVIDENCE[e.kind] ?? e.kind}
                <span className="text-muted-foreground">· {SOURCE[e.requested_by] ?? e.requested_by}</span>
              </Link>
            </li>
          ))}
        </ul>
      </li>

      <li className="relative grid gap-2">
        <span aria-hidden="true" className="absolute top-1 -left-[27px] size-3 rounded-full border-2 border-background bg-foreground" />
        <h4 className="text-base font-semibold">4. Conclusion</h4>
        <p className="text-sm">
          {root ? (
            <>
              <span className="font-medium">Root cause: </span>
              {templateLabel(root.template)} ({Math.round(Number(root.confidence) * 100)}% confidence)
            </>
          ) : (
            "No unplanned cause is supported by the evidence."
          )}
        </p>
        {explained.map((h) => (
          <p key={h.id} className="text-sm">
            <span className="font-medium">Explained by your context: </span>
            {templateLabel(h.template)} — a deliberate decision; its measured impact is unchanged.
          </p>
        ))}
      </li>
    </ol>
  );
}
