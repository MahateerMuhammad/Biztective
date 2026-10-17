import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { KindBadge } from "@/components/kind-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getEvidence } from "@/lib/api/server";

export const metadata: Metadata = { title: "Evidence" };

type Fact = { value: unknown; unit: string; kind?: string };
type SourceRow = { file: string; source_row: number; key: string; amount: string; detail: string | null };

function text(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean" ? String(value) : JSON.stringify(value);
}

/** One stored evidence item: the measurement, its parameters, its integrity and its sources. */
export default async function EvidencePage({ params }: PageProps<"/businesses/[businessId]/evidence/[evidenceId]">) {
  const { businessId, evidenceId } = await params;
  const item = await getEvidence(businessId, evidenceId);
  if (!item) notFound();
  const facts = (item.result.facts ?? null) as Record<string, Fact> | null;
  const sample = (Array.isArray(item.result.sample) ? item.result.sample : []) as SourceRow[];
  return (
    <div className="grid gap-6">
      <div>
        <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">
          Evidence: {item.kind.replaceAll("_", " ")} <KindBadge kind="fact" />
        </h1>
        <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          {item.intact ? (
            <Badge className="border-action-foreground/30 bg-action text-action-foreground">Integrity verified</Badge>
          ) : (
            <Badge className="border-risk-foreground/30 bg-risk text-risk-foreground">Integrity check failed</Badge>
          )}
          Recorded {item.created_at.slice(0, 19).replace("T", " ")} · query version {item.query_version}
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            <h2 className="text-base font-semibold">Measured values</h2>
          </CardTitle>
          <CardDescription>Computed by a fixed, versioned query over your stored records. The AI never produces these numbers.</CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          {facts ? (
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Facts in this evidence item</caption>
              <thead>
                <tr className="border-b">
                  <th scope="col" className="py-2 font-medium">Field</th>
                  <th scope="col" className="py-2 text-right font-medium">Value</th>
                  <th scope="col" className="py-2 font-medium">Unit</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(facts).map(([name, f]) => (
                  <tr key={name} className="border-b last:border-0">
                    <th scope="row" className="py-1.5 font-mono text-xs font-normal">
                      {name}
                    </th>
                    <td className="py-1.5 text-right tabular-nums">{text(f.value)}</td>
                    <td className="py-1.5 text-muted-foreground">{f.kind ?? f.unit}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <pre tabIndex={0} aria-label="Stored result" className="max-h-96 overflow-auto rounded-md bg-muted p-3 text-xs whitespace-pre-wrap break-all">
              {JSON.stringify(item.result, null, 2)}
            </pre>
          )}
        </CardContent>
      </Card>

      {sample.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>
              <h2 className="text-base font-semibold">Source records</h2>
            </CardTitle>
            <CardDescription>The rows of your uploaded files that contribute most, with their file and line.</CardDescription>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Contributing source records</caption>
              <thead>
                <tr className="border-b">
                  <th scope="col" className="py-2 font-medium">File</th>
                  <th scope="col" className="py-2 text-right font-medium">Row</th>
                  <th scope="col" className="py-2 font-medium">Record</th>
                  <th scope="col" className="py-2 text-right font-medium">Amount</th>
                  <th scope="col" className="py-2 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody>
                {sample.map((r) => (
                  <tr key={`${r.file}-${r.source_row}`} className="border-b last:border-0">
                    <td className="py-1.5">{r.file}.csv</td>
                    <td className="py-1.5 text-right tabular-nums">{r.source_row}</td>
                    <td className="py-1.5 font-mono text-xs">{r.key}</td>
                    <td className="py-1.5 text-right tabular-nums">{r.amount}</td>
                    <td className="py-1.5">{r.detail ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>
            <h2 className="text-base font-semibold">How it was computed</h2>
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-2 text-sm">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
            <dt className="text-muted-foreground">Result hash</dt>
            <dd className="font-mono text-xs break-all">{item.result_hash}</dd>
            <dt className="text-muted-foreground">Parameters hash</dt>
            <dd className="font-mono text-xs break-all">{item.params_hash}</dd>
          </dl>
          <details>
            <summary className="cursor-pointer font-medium">Parameters</summary>
            <pre tabIndex={0} aria-label="Query parameters" className="mt-2 max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs whitespace-pre-wrap break-all">
              {JSON.stringify(item.params, null, 2)}
            </pre>
          </details>
        </CardContent>
      </Card>
    </div>
  );
}
