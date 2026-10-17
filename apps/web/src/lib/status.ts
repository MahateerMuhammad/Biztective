/** Plain-language status words with a tone, shared by every list and detail page. */
import type { ActionStatus, InvestigationStatus, PlanStatus } from "@/lib/api/types";

export type Tone = "ok" | "warn" | "err" | "idle";
export interface Chip {
  label: string;
  tone: Tone;
}

const INVESTIGATION: Record<InvestigationStatus, Chip> = {
  queued: { label: "Waiting to start", tone: "idle" },
  running: { label: "In progress", tone: "idle" },
  awaiting_human: { label: "Needs your input", tone: "warn" },
  completed: { label: "Completed", tone: "ok" },
  completed_no_incidents: { label: "Nothing to explain", tone: "ok" },
  insufficient_data: { label: "Not enough data", tone: "warn" },
  failed: { label: "Couldn’t finish", tone: "err" },
};
export const investigationChip = (s: InvestigationStatus): Chip => INVESTIGATION[s] ?? { label: s, tone: "idle" };

const PLAN: Record<PlanStatus, Chip> = {
  queued: { label: "Preparing", tone: "idle" },
  running: { label: "Preparing", tone: "idle" },
  awaiting_decision: { label: "Needs your decision", tone: "warn" },
  approved: { label: "Approved", tone: "ok" },
  rejected: { label: "Rejected", tone: "idle" },
  failed: { label: "Couldn’t prepare", tone: "err" },
};
export const planChip = (s: PlanStatus): Chip => PLAN[s] ?? { label: s, tone: "idle" };

const EXECUTION: Record<ActionStatus, string> = {
  pending: "In progress",
  succeeded: "Written, checking",
  verified: "Verified in the sheet",
  failed: "Failed, nothing written",
  manual_review: "Needs review",
};
export const executionLabel = (s: ActionStatus): string => EXECUTION[s] ?? s;

export function datasetStatus(status: string): Chip {
  if (status === "ready") return { label: "Ready", tone: "ok" };
  if (status === "failed" || status === "rejected") return { label: status === "failed" ? "Couldn’t process" : "Rejected", tone: "err" };
  return { label: "Being checked", tone: "warn" };
}
