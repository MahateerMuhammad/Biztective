"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { postJson } from "@/lib/api/client";
import { ApiProblem } from "@/lib/api/types";

export function StartPlan({ businessId, investigationId }: { businessId: string; investigationId: string }) {
  const router = useRouter();
  // Kept across retries of this click so a lost response never creates two plans.
  const [key] = useState(() => crypto.randomUUID().replaceAll("-", ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const plan = await postJson<{ id: string }>(
        `/api/v1/businesses/${businessId}/investigations/${investigationId}/recovery-plans`,
        {},
        key,
      );
      if (plan) router.push(`/businesses/${businessId}/recovery-plans/${plan.id}`);
    } catch (caught) {
      setError(caught instanceof ApiProblem ? caught.message : "Could not create the recovery plan.");
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-2">
      <div>
        <Button type="button" onClick={start} disabled={busy}>
          {busy ? "Preparing…" : "Plan a recovery"}
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
