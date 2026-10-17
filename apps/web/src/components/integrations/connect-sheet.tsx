"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatDateTime } from "@/lib/format";
import { postJson } from "@/lib/api/client";
import { ApiProblem, type Integration } from "@/lib/api/types";
import { useHydrated } from "@/lib/use-hydrated";

type Bound = Integration & { verification_code: string; verify_cell: string };

/** Bind a spreadsheet and prove ownership with a one-time code (SEC-17). */
export function ConnectSheet({ businessId, pending }: { businessId: string; pending: Integration | null }) {
  const hydrated = useHydrated();
  const router = useRouter();
  const [spreadsheet, setSpreadsheet] = useState("");
  const [bound, setBound] = useState<Bound | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const target = bound ?? pending;

  async function bind(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setBound(await postJson<Bound>(`/api/v1/businesses/${businessId}/integrations/google-sheets`, { spreadsheet }));
    } catch (caught) {
      setError(caught instanceof ApiProblem ? caught.message : "Check your connection and try again.");
    }
    setBusy(false);
  }

  async function verify() {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      await postJson(`/api/v1/businesses/${businessId}/integrations/${target.id}/verify`, {});
      setBound(null);
      router.refresh();
    } catch (caught) {
      const detail = caught instanceof ApiProblem ? caught.message : "Check your connection and try again.";
      setError(detail);
    }
    setBusy(false);
  }

  return (
    <div className="grid gap-4 text-[15px]">
      {!target ? (
        <form className="grid gap-2" onSubmit={bind} aria-label="Connect a Google Sheet">
          <Label htmlFor="spreadsheet">Google Sheets link or spreadsheet ID</Label>
          <Input
            id="spreadsheet"
            value={spreadsheet}
            onChange={(e) => setSpreadsheet(e.target.value)}
            placeholder="https://docs.google.com/spreadsheets/d/…"
            required
            minLength={20}
            maxLength={300}
          />
          <div>
            <Button type="submit" disabled={!hydrated || busy}>
              {busy ? "Connecting…" : "Connect"}
            </Button>
          </div>
        </form>
      ) : (
        <ol className="grid list-decimal gap-2 pl-5" aria-label="Prove you own the spreadsheet">
          {target.service_account_email ? (
            <li>
              Share the spreadsheet with <span className="tabular-nums">{target.service_account_email}</span> as an Editor.
            </li>
          ) : null}
          <li>
            Add a tab named <span className="tabular-nums">ROOT_VERIFY</span> and type{" "}
            {bound ? (
              <strong className="tabular-nums">{bound.verification_code}</strong>
            ) : (
              "the code you were shown when you connected"
            )}{" "}
            into cell A1. {bound ? "This code is shown only once." : "If you no longer have it, connect again."} It expires at{" "}
            {formatDateTime(target.verify_expires_at)} UTC.
          </li>
          <li>
            <Button type="button" onClick={verify} disabled={busy}>
              {busy ? "Checking…" : "Check and verify"}
            </Button>
          </li>
        </ol>
      )}
      {error ? (
        <Alert variant="destructive">
          <AlertTitle>Not connected</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}
