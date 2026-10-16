import type { Segment } from "@/lib/api/types";

/**
 * Server-rendered text as plain React text: values come from stored evidence (with the
 * evidence ID and field on hover), entities from server-side labels. Nothing is interpreted
 * as markup.
 */
export function Segments({ segments }: { segments: Segment[] }) {
  return (
    <>
      {segments.map((segment, i) => {
        if ("value" in segment) {
          return (
            <span
              key={i}
              className="font-medium tabular-nums"
              title={`From evidence ${segment.evidence_id} (${segment.field})`}
            >
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
