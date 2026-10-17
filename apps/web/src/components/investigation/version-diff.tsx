import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import type { VersionComparison } from "@/lib/api/types";
import { STATUS_LABELS, templateLabel } from "@/lib/format";

const EFFECT_LABELS: Record<string, string> = {
  confirm: "confirmed",
  deny: "denied",
  intended: "said it was a deliberate decision",
  unsure: "was not sure",
  unstructured: "added a comment",
};

function describe(field: string, value: unknown): string {
  if (value === null || value === undefined) return "none";
  if (field === "status") return STATUS_LABELS[String(value)] ?? String(value);
  if (field === "confidence" || field === "human_confidence") return `${(Number(value) * 100).toFixed(0)}%`;
  if (Array.isArray(value)) return value.length ? value.join(", ") : "none";
  return String(value);
}

const FIELD_LABELS: Record<string, string> = {
  status: "Status",
  confidence: "Confidence",
  human_confidence: "Your context",
  interpretation: "Interpretation",
  conflicts: "Conflicts with data",
};

/** What one human answer changed in Biztective's interpretation (a pure diff of stored versions). */
export function VersionDiff({ comparison }: { comparison: VersionComparison }) {
  const changed = comparison.incidents.filter(
    (i) =>
      i.root_cause ||
      (i.hypotheses?.length ?? 0) > 0 ||
      (i.recommendations?.added.length ?? 0) + (i.recommendations?.removed.length ?? 0) > 0 ||
      (i.explained_by_context?.added.length ?? 0) > 0 ||
      i.measurements_preserved === false,
  );
  return (
    <div className="grid gap-4">
      {comparison.measurements_preserved ? (
        <p role="status" className="text-sm">
          <span aria-hidden="true">✓ </span>All original measurements are unchanged (impacts and
          evidence hashes of version {comparison.from_version} are identical in version{" "}
          {comparison.to_version}).
        </p>
      ) : (
        <Alert variant="destructive" role="alert">
          <AlertTitle>Measurements differ between these versions</AlertTitle>
          <AlertDescription>
            A stored result no longer matches its recorded hash. Treat these figures as unverified.
          </AlertDescription>
        </Alert>
      )}
      {comparison.caused_by ? (
        <p className="text-sm text-muted-foreground">
          Caused by an answer to {comparison.caused_by.criterion}: the person{" "}
          {EFFECT_LABELS[comparison.caused_by.effect] ?? comparison.caused_by.effect}
          {comparison.caused_by.option_id ? ` (${comparison.caused_by.option_id})` : ""}.
        </p>
      ) : null}
      {comparison.findings.summary ? (
        <div className="grid gap-1 text-sm">
          <p>
            <span className="font-medium">Summary before: </span>
            {comparison.findings.summary.from}
          </p>
          <p>
            <span className="font-medium">Summary after: </span>
            {comparison.findings.summary.to}
          </p>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">The summary did not change.</p>
      )}
      {changed.length === 0 ? (
        <p className="text-sm">No hypothesis, root cause or recommendation changed.</p>
      ) : (
        <ul className="grid gap-4" aria-label="Changes per incident">
          {changed.map((incident) => (
            <li key={incident.key} className="grid gap-2 rounded-md border p-3 text-sm">
              <p className="tabular-nums text-[13px]">{incident.key}</p>
              {incident.root_cause ? (
                <p>
                  Root cause changed: <strong>{templateLabel(incident.root_cause.from)}</strong> →{" "}
                  <strong>{templateLabel(incident.root_cause.to)}</strong>
                </p>
              ) : null}
              {incident.explained_by_context?.added.length ? (
                <p>
                  Now explained by a business decision:{" "}
                  {incident.explained_by_context.added.map(templateLabel).join(", ")}
                </p>
              ) : null}
              {incident.hypotheses?.length ? (
                <ul className="grid gap-1" aria-label="Hypothesis changes">
                  {incident.hypotheses.map((h) => (
                    <li key={h.template}>
                      {h.added ? (
                        <>
                          New alternative investigated: <strong>{templateLabel(h.template)}</strong> (
                          {describe("status", h.now?.status)})
                        </>
                      ) : (
                        <>
                          <strong>{templateLabel(h.template)}</strong>:{" "}
                          {Object.entries(h.changes ?? {})
                            .map(
                              ([field, c]) =>
                                `${FIELD_LABELS[field] ?? field} ${describe(field, c.from)} → ${describe(field, c.to)}`,
                            )
                            .join("; ")}
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              ) : null}
              {incident.recommendations?.added.length || incident.recommendations?.removed.length ? (
                <div className="grid gap-1">
                  {incident.recommendations.removed.map((r) => (
                    <p key={`-${r}`}>
                      <span className="font-medium">No longer recommended: </span>
                      {r}
                    </p>
                  ))}
                  {incident.recommendations.added.map((r) => (
                    <p key={`+${r}`}>
                      <span className="font-medium">Now recommended: </span>
                      {r}
                    </p>
                  ))}
                </div>
              ) : null}
              {incident.measurements_preserved === false ? (
                <p role="alert">Measurements of this incident changed.</p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
