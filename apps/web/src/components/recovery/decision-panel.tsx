"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { postJson } from "@/lib/api/client";
import { ApiProblem, type ActionType, type PlanDetail } from "@/lib/api/types";
import { formatMoney, formatPercent } from "@/lib/format";
import { useHydrated } from "@/lib/use-hydrated";

/** The plan changed under the person: reload instead of retrying. */
const RELOAD_CODES = new Set([
  "stale_plan_version",
  "content_hash_mismatch",
  "stale_checkpoint",
  "already_decided",
  "invalid_state",
]);
const REASON_MAX = 1000;
type Mode = "approve" | "reject" | "changes" | null;

function newKey(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

function lines(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Percent typed by a person ("12.5") → ratio string ("0.125"), at most 4 decimals. */
export function toShare(percent: string): string {
  const ratio = Number(percent) / 100;
  return String(Number(ratio.toFixed(4)));
}

export function buildEdits(
  plan: PlanDetail,
  form: {
    budget: string;
    maxDiscount: string;
    horizon: string;
    allowed: ActionType[];
    regions: string;
    channels: string;
    selected: string[];
    costs: Record<string, Record<string, { low: string; mid: string; high: string }>>;
    targets: Record<string, string>;
  },
): Record<string, unknown> {
  const c = plan.current!.content;
  const edits: Record<string, unknown> = {};
  const budget = form.budget.trim() === "" ? null : form.budget.trim();
  const before = c.constraints.budget_amount;
  if ((budget === null) !== (before === null) || (budget !== null && Number(budget) !== Number(before))) {
    edits.budget_amount = budget;
  }
  const share = toShare(form.maxDiscount);
  if (Number(share) !== Number(c.constraints.max_discount_pct)) edits.max_discount_pct = share;
  if (Number(form.horizon) !== c.constraints.horizon_weeks) edits.horizon_weeks = Number(form.horizon);
  const same = (a: string[], b: string[]) => a.length === b.length && [...a].sort().every((x, i) => x === [...b].sort()[i]);
  if (!same(form.allowed, c.constraints.allowed_action_types)) edits.allowed_action_types = form.allowed;
  if (!same(lines(form.regions), c.constraints.excluded_regions)) edits.excluded_regions = lines(form.regions);
  if (!same(lines(form.channels), c.constraints.excluded_channels)) edits.excluded_channels = lines(form.channels);
  if (!same(form.selected, c.selected)) edits.selected_strategies = form.selected;
  const assumptions: Record<string, Record<string, { low: string; mid: string; high: string }>> = {};
  for (const [instance, values] of Object.entries(form.costs)) {
    const strategy = c.strategies.find((s) => s.id === instance);
    for (const [key, range] of Object.entries(values)) {
      const current = strategy?.assumptions[key];
      if (!range.low && !range.mid && !range.high) continue;
      if (current && current.low === range.low && current.mid === range.mid && current.high === range.high) continue;
      (assumptions[instance] ??= {})[key] = range;
    }
  }
  if (Object.keys(assumptions).length) edits.assumptions = assumptions;
  const targets: Record<string, string[]> = {};
  for (const [instance, text] of Object.entries(form.targets)) {
    const strategy = c.strategies.find((s) => s.id === instance);
    const ids = lines(text);
    if (strategy?.targeting && !same(ids, strategy.targeting.selected)) targets[instance] = ids;
  }
  if (Object.keys(targets).length) edits.targets = targets;
  return edits;
}

function Field({ id, label, children, help }: { id: string; label: string; children: ReactNode; help?: string }) {
  return (
    <div className="grid gap-1">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {help ? <p className="text-xs text-muted-foreground">{help}</p> : null}
    </div>
  );
}

export function DecisionPanel({ businessId, plan, role }: { businessId: string; plan: PlanDetail; role: string }) {
  const router = useRouter();
  const current = plan.current!;
  const c = current.content;
  const awaiting = plan.awaiting;
  const approved = plan.status === "approved";
  const [mode, setMode] = useState<Mode>(null);
  const hydrated = useHydrated();
  const [reason, setReason] = useState("");
  const [key, setKey] = useState(newKey);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ title: string; detail: string } | null>(null);
  const [budget, setBudget] = useState(c.constraints.budget_amount ?? "");
  const [maxDiscount, setMaxDiscount] = useState(String(Number(c.constraints.max_discount_pct) * 100));
  const [horizon, setHorizon] = useState(String(c.constraints.horizon_weeks));
  const [allowed, setAllowed] = useState<ActionType[]>(c.constraints.allowed_action_types);
  const [regions, setRegions] = useState(c.constraints.excluded_regions.join(", "));
  const [channels, setChannels] = useState(c.constraints.excluded_channels.join(", "));
  const [selected, setSelected] = useState<string[]>(c.selected);
  const [costs, setCosts] = useState<Record<string, Record<string, { low: string; mid: string; high: string }>>>({});
  const [targets, setTargets] = useState<Record<string, string>>(
    Object.fromEntries(c.strategies.filter((s) => s.targeting).map((s) => [s.id, s.targeting!.selected.join("\n")])),
  );
  if (role === "viewer" || (!awaiting && !approved)) return null;

  function changed(update: () => void) {
    update();
    setKey(newKey());
    setError(null);
  }

  const edits = buildEdits(plan, { budget, maxDiscount, horizon, allowed, regions, channels, selected, costs, targets });
  const chosen = c.strategies.filter((s) => c.selected.includes(s.id));

  async function submit(kind: "approve" | "reject" | "request-changes" | "revisions", event?: FormEvent) {
    event?.preventDefault();
    setBusy(true);
    setError(null);
    const base = awaiting
      ? { version: awaiting.version, content_hash: awaiting.content_hash, checkpoint_id: awaiting.checkpoint_id }
      : { version: current.version, content_hash: current.content_hash };
    const body: Record<string, unknown> = { ...base };
    if (reason.trim()) body.reason = reason.trim();
    if (kind === "request-changes" || kind === "revisions") body.edits = edits;
    try {
      await postJson(`/api/v1/businesses/${businessId}/recovery-plans/${plan.id}/${kind}`, body, key);
      setMode(null);
      router.refresh();
    } catch (caught) {
      if (caught instanceof ApiProblem && RELOAD_CODES.has(caught.code)) {
        setError({ title: "This plan has changed", detail: `${caught.message} Showing the latest version.` });
        router.refresh();
      } else {
        setError({
          title: "Your decision was not saved",
          detail: caught instanceof ApiProblem ? caught.message : "Check your connection and try again.",
        });
      }
    }
    setBusy(false);
  }

  const hashPrefix = (awaiting?.content_hash ?? current.content_hash).slice(0, 12);
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap gap-2">
        {awaiting && role === "owner" ? (
          <Button type="button" onClick={() => changed(() => setMode("approve"))} disabled={!awaiting.can_approve || busy}>
            Review approval
          </Button>
        ) : null}
        {awaiting ? (
          <Button type="button" variant="outline" onClick={() => changed(() => setMode("reject"))} disabled={busy}>
            Reject plan
          </Button>
        ) : null}
        <Button type="button" variant="outline" onClick={() => changed(() => setMode("changes"))} disabled={busy}>
          {approved ? "Edit approved plan" : "Request changes"}
        </Button>
      </div>
      {awaiting && role === "owner" && !awaiting.can_approve ? (
        <p className="text-sm text-muted-foreground">This version breaks a constraint, so it cannot be approved. Request changes first.</p>
      ) : null}
      {awaiting && role !== "owner" ? (
        <p className="text-sm text-muted-foreground">Only an owner can approve. You can reject or request changes.</p>
      ) : null}

      {mode === "approve" && awaiting ? (
        <section role="dialog" aria-modal="false" aria-labelledby="approve-title" className="grid gap-3 rounded-md border p-4 text-sm">
          <h3 id="approve-title" className="font-semibold">
            Approve version {awaiting.version}
          </h3>
          <p>
            Content hash <span className="font-mono">{hashPrefix}…</span>. Your approval covers exactly this version; any later
            change creates a new version that needs a new approval. Approving records your decision only: ROOT executes nothing.
          </p>
          <ul className="list-disc pl-5" aria-label="Strategies you are approving">
            {chosen.map((s) => (
              <li key={s.id}>
                {s.title} <span className="font-mono text-xs">({s.incident.key})</span> · actions: {s.action_types.join(", ")}
              </li>
            ))}
          </ul>
          <p>
            Budget {formatMoney(c.constraints.budget_amount, c.currency)} · highest estimated cost{" "}
            {formatMoney(c.totals?.scenarios.high.cost ?? null, c.currency)} · horizon {c.constraints.horizon_weeks} weeks · permitted
            actions: {c.constraints.allowed_action_types.join(", ")}
          </p>
          <details>
            <summary>Exact plan content</summary>
            <pre tabIndex={0} aria-label="Exact plan content" className="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(c, null, 2)}</pre>
          </details>
          <div className="flex gap-2">
            <Button type="button" onClick={() => submit("approve")} disabled={busy}>
              {busy ? "Approving…" : "Confirm approval"}
            </Button>
            <Button type="button" variant="outline" onClick={() => setMode(null)}>
              Cancel
            </Button>
          </div>
        </section>
      ) : null}

      {mode === "reject" ? (
        <form className="grid gap-3 rounded-md border p-4 text-sm" onSubmit={(e) => submit("reject", e)} aria-label="Reject plan">
          <Field id="reject-reason" label="Why are you rejecting it? (optional)">
            <textarea
              id="reject-reason"
              className="min-h-16 rounded-md border bg-background p-2"
              maxLength={REASON_MAX}
              value={reason}
              onChange={(e) => {
                const value = e.target.value;
                changed(() => setReason(value));
              }}
            />
          </Field>
          <p className="text-xs text-muted-foreground">Rejecting is final for this plan and has no effect outside ROOT.</p>
          <div>
            <Button type="submit" disabled={!hydrated || busy}>
              {busy ? "Rejecting…" : "Confirm rejection"}
            </Button>
          </div>
        </form>
      ) : null}

      {mode === "changes" ? (
        <form
          className="grid gap-4 rounded-md border p-4 text-sm"
          onSubmit={(e) => submit(approved ? "revisions" : "request-changes", e)}
          aria-label="Change the plan"
        >
          {approved ? (
            <Alert>
              <AlertTitle>Editing removes the approval</AlertTitle>
              <AlertDescription>The changed plan becomes a new version that needs a new approval.</AlertDescription>
            </Alert>
          ) : null}
          <div className="grid gap-3 sm:grid-cols-3">
            <Field id="budget" label={`Budget (${c.currency})`} help="Leave empty for no budget.">
              <Input id="budget" inputMode="decimal" value={budget} onChange={(e) => { const v = e.target.value; changed(() => setBudget(v)); }} />
            </Field>
            <Field id="max-discount" label="Maximum discount (%)">
              <Input id="max-discount" type="number" min={0} max={50} step="0.5" value={maxDiscount} onChange={(e) => { const v = e.target.value; changed(() => setMaxDiscount(v)); }} />
            </Field>
            <Field id="horizon" label="Horizon (weeks)">
              <Input id="horizon" type="number" min={1} max={12} value={horizon} onChange={(e) => { const v = e.target.value; changed(() => setHorizon(v)); }} />
            </Field>
          </div>
          <fieldset className="grid gap-1">
            <legend className="font-medium">Permitted actions</legend>
            {(Object.keys(plan.catalog.action_types) as ActionType[]).map((a) => (
              <label key={a} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  className="size-4"
                  checked={allowed.includes(a)}
                  onChange={() => changed(() => setAllowed((prev) => (prev.includes(a) ? prev.filter((x) => x !== a) : [...prev, a])))}
                />
                {plan.catalog.action_types[a].label}
                {plan.catalog.action_types[a].risky ? <span className="text-xs font-medium">(external effect, opt-in)</span> : null}
              </label>
            ))}
          </fieldset>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field id="regions" label="Excluded regions" help="Comma-separated.">
              <Input id="regions" value={regions} onChange={(e) => { const v = e.target.value; changed(() => setRegions(v)); }} />
            </Field>
            <Field id="channels" label="Excluded acquisition channels" help="Comma-separated.">
              <Input id="channels" value={channels} onChange={(e) => { const v = e.target.value; changed(() => setChannels(v)); }} />
            </Field>
          </div>
          <fieldset className="grid gap-3">
            <legend className="font-medium">Strategies</legend>
            {c.strategies.map((s) => {
              const money = Object.entries(s.assumptions).filter(([, a]) => a.unit === "money");
              const on = selected.includes(s.id);
              return (
                <div key={s.id} className="grid gap-2 rounded-md border p-2">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      className="size-4"
                      checked={on}
                      onChange={() => changed(() => setSelected((prev) => (prev.includes(s.id) ? prev.filter((x) => x !== s.id) : [...prev, s.id])))}
                    />
                    #{s.rank} {s.title} <span className="font-mono text-xs">({s.incident.key})</span>
                  </label>
                  {on
                    ? money.map(([k, a]) => (
                        <fieldset key={k} className="grid gap-1 sm:grid-cols-3">
                          <legend className="text-xs">{a.label} ({c.currency})</legend>
                          {(["low", "mid", "high"] as const).map((sc) => {
                            const id = `cost-${s.rank}-${k}-${sc}`;
                            const value = costs[s.id]?.[k]?.[sc] ?? a[sc] ?? "";
                            return (
                              <Field key={sc} id={id} label={`${a.label} (${sc})`}>
                                <Input
                                  id={id}
                                  inputMode="decimal"
                                  value={value}
                                  onChange={(e) => {
                                    const v = e.target.value;
                                    changed(() =>
                                      setCosts((prev) => {
                                        const before = prev[s.id]?.[k] ?? { low: a.low ?? "", mid: a.mid ?? "", high: a.high ?? "" };
                                        return { ...prev, [s.id]: { ...prev[s.id], [k]: { ...before, [sc]: v } } };
                                      }),
                                    );
                                  }}
                                />
                              </Field>
                            );
                          })}
                        </fieldset>
                      ))
                    : null}
                  {on && s.targeting ? (
                    <Field id={`targets-${s.rank}`} label="Customers to target (one ID per line)" help={`${s.targeting.eligible_customers} eligible customers.${s.discount ? ` Offer discount ${formatPercent(s.discount)}.` : ""}`}>
                      <textarea
                        id={`targets-${s.rank}`}
                        className="min-h-20 rounded-md border bg-background p-2 font-mono text-xs"
                        value={targets[s.id] ?? ""}
                        onChange={(e) => {
                          const v = e.target.value;
                          changed(() => setTargets((prev) => ({ ...prev, [s.id]: v })));
                        }}
                      />
                    </Field>
                  ) : null}
                </div>
              );
            })}
          </fieldset>
          <Field id="changes-reason" label="Note for your team (optional)">
            <textarea
              id="changes-reason"
              className="min-h-16 rounded-md border bg-background p-2"
              maxLength={REASON_MAX}
              value={reason}
              onChange={(e) => {
                const value = e.target.value;
                changed(() => setReason(value));
              }}
            />
          </Field>
          <div>
            <Button type="submit" disabled={!hydrated || busy || Object.keys(edits).length === 0}>
              {busy ? "Saving…" : approved ? "Save as a new version" : "Recalculate with these changes"}
            </Button>
          </div>
        </form>
      ) : null}

      {error ? (
        <Alert variant="destructive" role="alert">
          <AlertTitle>{error.title}</AlertTitle>
          <AlertDescription>{error.detail}</AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}
