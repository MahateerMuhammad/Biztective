import type { InvestigationEvent } from "@/lib/api/types";

/** Plain-language labels for the investigation's append-only event log. */
const LABELS: Record<string, string> = {
  "run.started": "Investigation started",
  "incidents.detected": "Incidents loaded from the analysis",
  "incident.selected": "Looking at an incident",
  "hypotheses.generated": "Competing explanations listed",
  "hypotheses.added": "Explanation added after your answer",
  "tools.selected": "Chose which checks to run",
  "tools.limit_reached": "Check limit reached for this incident",
  "tool.rejected": "A proposed check was refused by the safety rules",
  "tool.failed": "A check failed and was skipped",
  "evidence.collected": "Evidence collected",
  "evidence.looked_up": "Re-read stored evidence",
  "evidence.limit_reached": "Evidence limit reached",
  "evidence.integrity_failed": "Evidence failed its integrity check and was excluded",
  "hypothesis.evaluated": "An explanation was scored against the evidence",
  "findings.drafted": "Findings drafted",
  "clarification.requested": "Asked you a question",
  "clarification.cap_reached": "Question limit reached",
  "human.context_added": "Your answer was recorded",
  "human.context_applied": "Your answer was applied",
  "version.created": "New version of the conclusions",
  "llm.disabled": "AI wording off: deterministic text used",
  "llm.fallback_used": "AI output rejected: deterministic text used",
  "investigation.final": "Investigation finished",
};

/** Newest first, so the latest step is on top while the agent works. */
export function Progress({ events, working }: { events: InvestigationEvent[]; working: boolean }) {
  const recent = [...events].reverse().slice(0, 12);
  if (recent.length === 0) return <p className="text-sm text-muted-foreground">Waiting for the agent to start…</p>;
  return (
    <ol className="grid gap-2 text-sm" aria-label="Agent activity, newest first">
      {recent.map((e, i) => (
        <li key={e.seq} className="flex gap-3">
          <span
            aria-hidden="true"
            className={`mt-1.5 size-2 shrink-0 rounded-full ${i === 0 && working ? "bg-fact-foreground" : "bg-muted-foreground/40"}`}
          />
          <span className="grid">
            <span className={i === 0 ? "font-medium" : ""}>{LABELS[e.type] ?? e.type.replaceAll(/[._]/g, " ")}</span>
            <span className="text-xs text-muted-foreground tabular-nums">
              {e.occurred_at.slice(11, 19)} · step {e.seq}
              {e.actor_kind === "human" ? " · by a person" : ""}
            </span>
          </span>
        </li>
      ))}
    </ol>
  );
}
