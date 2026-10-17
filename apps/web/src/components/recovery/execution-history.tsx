import type { ActionDetail, ActionStatus } from "@/lib/api/types";

export const ACTION_STATUS_TEXT: Record<ActionStatus, string> = {
  pending: "In progress: the request is recorded and the sheet is being updated",
  succeeded: "Written; checking the sheet",
  verified: "Done and verified: every row was read back from the sheet and matches",
  failed: "Failed: nothing was written to the sheet",
  manual_review: "Needs review: the sheet may be partly updated or was changed by someone",
};

const ERROR_TEXT: Record<string, string> = {
  approval_no_longer_valid: "The approval no longer covers this version (the plan was edited or re-decided).",
  content_changed: "The approved content changed.",
  approver_not_owner: "The person who approved is no longer an owner.",
  requester_not_owner: "The person who started it is no longer an owner.",
  integration_not_verified: "The spreadsheet was disconnected.",
  integration_credentials_invalid: "Google refused ROOT's credentials. An administrator must renew them.",
  integration_permission_denied: "ROOT can no longer edit the spreadsheet. Share it again with the service account.",
  destination_not_found: "The spreadsheet no longer exists.",
  destination_layout_mismatch: "A tab with ROOT's name has different columns. Rename that tab and retry.",
  destination_invalid: "Google refused the request for this spreadsheet.",
  verification_mismatch: "Rows in the sheet differ from what was approved, or appear twice. ROOT did not overwrite them.",
  write_unconfirmed: "Google did not confirm the write after several tries.",
  network_error: "Google could not be reached.",
  rate_limited: "Google asked ROOT to slow down.",
  provider_unavailable: "Google reported a server error.",
  timeout: "Google did not answer in time.",
  attempt_timeout: "The attempt took too long.",
  worker_lost: "The server stopped during this attempt; the next attempt checked the sheet first.",
};

function when(value: string | null): string {
  return value ? value.slice(0, 19).replace("T", " ") : "—";
}

export function ExecutionHistory({ actions }: { actions: ActionDetail[] }) {
  return (
    <div className="grid gap-6">
      {actions.map((a) => (
        <article key={a.id} className="grid gap-3 text-sm" aria-labelledby={`action-${a.id}`}>
          <h3 id={`action-${a.id}`} className="font-semibold">
            Version {a.plan_version} → Google Sheets
          </h3>
          <p role="status">
            <strong>{ACTION_STATUS_TEXT[a.status]}</strong>
            {a.error_code ? <span className="block text-muted-foreground">{ERROR_TEXT[a.error_code] ?? a.error_code}</span> : null}
          </p>
          <p>
            <a href={a.destination.url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-4">
              Open the spreadsheet
            </a>{" "}
            · action <span className="font-mono text-xs">{a.id}</span> · payload hash{" "}
            <span className="font-mono text-xs">{a.payload_hash.slice(0, 12)}…</span>
          </p>
          <table className="w-full text-left">
            <caption className="sr-only">Rows per tab</caption>
            <thead>
              <tr>
                <th scope="col">Tab</th>
                <th scope="col">Rows to write</th>
                <th scope="col">Found on read-back</th>
                <th scope="col">Written range</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(a.rows).map(([tab, count]) => (
                <tr key={tab}>
                  <td>{tab}</td>
                  <td>{count}</td>
                  <td>{a.verification?.tabs[tab]?.found ?? "—"}</td>
                  <td className="font-mono text-xs">{a.external_ref?.updated_ranges[tab] ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {a.verification && !a.verification.ok ? (
            <ul className="list-disc pl-5" aria-label="Verification problems">
              {a.verification.missing.length ? <li>Missing rows: {a.verification.missing.join(", ")}</li> : null}
              {a.verification.duplicated.length ? <li>Rows found twice: {a.verification.duplicated.join(", ")}</li> : null}
              {a.verification.mismatched.length ? <li>Rows changed: {a.verification.mismatched.join(", ")}</li> : null}
              {a.verification.unexpected.length ? <li>Unexpected rows: {a.verification.unexpected.join(", ")}</li> : null}
            </ul>
          ) : null}
          {a.not_executed.length ? (
            <p className="text-muted-foreground">
              Not carried out by ROOT (do these yourself if you still want them):{" "}
              {a.not_executed.map((n) => `${n.strategy} · ${n.action_type.replaceAll("_", " ")}`).join("; ")}.
            </p>
          ) : null}
          <details>
            <summary>Attempts and Google calls ({a.attempts.length})</summary>
            <table className="mt-2 w-full text-left">
              <caption className="sr-only">Execution attempts</caption>
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">Started</th>
                  <th scope="col">Write sent</th>
                  <th scope="col">Calls (operation:status)</th>
                </tr>
              </thead>
              <tbody>
                {a.attempts.map((t) => (
                  <tr key={t.attempt}>
                    <td>{t.attempt}</td>
                    <td>
                      {t.status}
                      {t.error_code ? ` (${t.error_code})` : ""}
                    </td>
                    <td>{when(t.started_at)}</td>
                    <td>{t.writes_sent ? "yes" : "no"}</td>
                    <td className="font-mono text-xs">{t.calls.map((c) => `${c.op}:${c.status}`).join(" ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
          <details>
            <summary>Audit trail ({a.audit.length})</summary>
            <ol className="mt-2 grid gap-1" aria-label="Audit entries">
              {a.audit.map((e, i) => (
                <li key={i}>
                  {when(e.at)} · {e.action} · {e.actor_user_id ? "by a person" : "by the executor"}
                </li>
              ))}
            </ol>
          </details>
        </article>
      ))}
    </div>
  );
}
