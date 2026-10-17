"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { postJson } from "@/lib/api/client";
import { ApiProblem, type ActionSummary } from "@/lib/api/types";

function newKey(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

/** Owner controls for running an approved plan in the connected Google Sheet (AMD-18). */
export function ExecutionPanel({
  businessId,
  planId,
  approved,
  integrationReady,
  current,
  role,
}: {
  businessId: string;
  planId: string;
  approved: { version: number; content_hash: string } | null;
  integrationReady: boolean;
  current: ActionSummary | null;
  role: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One key per page view: a double click or a network retry replays the same request.
  const [key] = useState(newKey);
  if (role !== "owner" || !approved) return null;

  async function run(path: string, body: unknown, idempotencyKey?: string) {
    setBusy(true);
    setError(null);
    try {
      await postJson(path, body, idempotencyKey);
      router.refresh();
    } catch (caught) {
      setError(caught instanceof ApiProblem ? caught.message : "Check your connection and try again.");
    }
    setBusy(false);
  }

  const retryable = current && (current.status === "failed" || current.status === "manual_review");
  return (
    <div className="grid gap-3 text-sm">
      {!integrationReady && !current ? (
        <p>
          Connect a Google Sheet first.{" "}
          <Link href={`/businesses/${businessId}/integrations`} className="underline underline-offset-4">
            Connect a spreadsheet
          </Link>
        </p>
      ) : null}
      {integrationReady && !current ? (
        <>
          <p>
            Biztective will add the approved review tasks and the customer-service queue for version {approved.version} to your
            connected spreadsheet, then read them back to check them. No customer is contacted.
          </p>
          <div>
            <Button
              type="button"
              disabled={busy}
              onClick={() =>
                run(
                  `/api/v1/businesses/${businessId}/recovery-plans/${planId}/actions`,
                  { version: approved.version, content_hash: approved.content_hash },
                  key,
                )
              }
            >
              {busy ? "Starting…" : "Execute in Google Sheets"}
            </Button>
          </div>
        </>
      ) : null}
      {retryable ? (
        <div>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => run(`/api/v1/businesses/${businessId}/actions/${current.id}/retry`, {})}
          >
            {busy ? "Retrying…" : "Retry (checks the sheet first)"}
          </Button>
        </div>
      ) : null}
      {error ? (
        <Alert variant="destructive">
          <AlertTitle>Could not confirm the request</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}
