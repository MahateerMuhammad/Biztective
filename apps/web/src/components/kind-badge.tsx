import { Badge } from "@/components/ui/badge";

/** What kind of statement a line is, so facts, hypotheses, estimates, people's input and
 * actions are never confused (Phase 9). The word is always shown; colour is only a cue. */
const KINDS = {
  fact: {
    label: "Fact",
    title: "Measured from your data",
    className: "border-fact-foreground/20 bg-fact text-fact-foreground",
  },
  hypothesis: {
    label: "Hypothesis",
    title: "A possible explanation, tested against the data",
    className: "border-hypothesis-foreground/20 bg-hypothesis text-hypothesis-foreground",
  },
  estimate: {
    label: "Estimate",
    title: "Computed from assumptions, not observed",
    className: "border-estimate-foreground/20 bg-estimate text-estimate-foreground",
  },
  human: {
    label: "Your input",
    title: "Context a person provided",
    className: "border-human-foreground/20 bg-human text-human-foreground",
  },
  action: {
    label: "Action",
    title: "Something that changes things outside ROOT",
    className: "border-action-foreground/20 bg-action text-action-foreground",
  },
} as const;

export type Kind = keyof typeof KINDS;

export function KindBadge({ kind }: { kind: Kind }) {
  const k = KINDS[kind];
  return (
    <Badge className={k.className} title={k.title}>
      {k.label}
    </Badge>
  );
}
