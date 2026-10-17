import Link from "next/link";

import type { Segment } from "@/lib/api/types";

/**
 * Server-rendered text as plain React text: values come from stored evidence, entities from
 * server-side labels. Nothing is interpreted as markup. With `evidenceBase`, every cited value
 * links to the evidence item it was read from (its source reference).
 */
export function Segments({ segments, evidenceBase }: { segments: Segment[]; evidenceBase?: string }) {
  return (
    <>
      {segments.map((segment, i) => {
        if ("value" in segment) {
          const title = `From evidence ${segment.evidence_id} (${segment.field})`;
          return evidenceBase ? (
            <Link
              key={i}
              href={`${evidenceBase}/${segment.evidence_id}`}
              title={title}
              className="font-medium tabular-nums underline decoration-dotted underline-offset-4 hover:decoration-solid"
            >
              {segment.value}
            </Link>
          ) : (
            <span key={i} className="font-medium tabular-nums" title={title}>
              {segment.value}
            </span>
          );
        }
        if ("entity" in segment) {
          return (
            <span key={i} className="font-medium">
              {segment.entity}
            </span>
          );
        }
        return <span key={i}>{segment.text}</span>;
      })}
    </>
  );
}
