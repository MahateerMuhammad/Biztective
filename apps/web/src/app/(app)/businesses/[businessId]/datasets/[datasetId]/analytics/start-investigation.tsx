"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { postJson } from "@/lib/api/client";
import { ApiProblem, type InvestigationSummary } from "@/lib/api/types";

export function StartInvestigation({ businessId, datasetId }: { businessId: string; datasetId: string }) {
  const router = useRouter();
  // Kept across retries of this click so a lost response never starts two investigations.
  const [key] = useState(() => crypto.randomUUID().replaceAll("-", ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const investigation = await postJson<InvestigationSummary>(
        `/api/v1/businesses/${businessId}/investigations`,
        { dataset_id: datasetId },
        key,
      );
      if (investigation) router.push(`/businesses/${businessId}/investigations/${investigation.id}`);
    } catch (caught) {
      setError(caught instanceof ApiProblem ? caught.message : "Could not start the investigation.");
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-2">
      <div>
        <Button type="button" onClick={start} disabled={busy}>
          {busy ? "Starting…" : "Start investigation"}
        </Button>
      </div>
      {error ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}
