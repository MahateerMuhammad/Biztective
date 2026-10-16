import { Segments } from "@/components/segments";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { ActionType, PlanStrategy } from "@/lib/api/types";
import { BREAK_EVEN, INELIGIBLE, PLAN_ISSUES, formatMoney, formatPercent, templateLabel } from "@/lib/format";

const OBSERVED_LABELS: Record<string, string> = {
  impact: "Measured financial impact of the incident",
  "net_revenue.current": "Net revenue in the current window",
  "gross_profit.current": "Gross profit in the current window",
  "contribution_margin.current": "Contribution margin",
  "discount_rate.current": "Discount rate now",
  "discount_rate.baseline": "Discount rate before",
  "aov.current": "Average order value",
};
const SOURCE: Record<string, string> = { default: "default assumption", human: "your input", missing: "needed from you" };

function observedValue(unit: string, value: string | null, currency: string): string {
  if (unit === "money") return formatMoney(value, currency);
  if (unit === "ratio") return formatPercent(value);
  return value ?? "—";
}

export function StrategyCard({
  strategy,
  currency,
  selected,
  actionLabels,
}: {
  strategy: PlanStrategy;
  currency: string;
  selected: boolean;
  actionLabels: Record<ActionType, { risky: boolean; label: string }>;
}) {
  const estimate = strategy.estimate;
  const shown = strategy.observed.filter((o) => o.name in OBSERVED_LABELS);
  return (
    <Card aria-labelledby={`strategy-${strategy.rank}`}>
      <CardHeader>
        <CardTitle>
          <h3 id={`strategy-${strategy.rank}`} className="text-base font-semibold">
            #{strategy.rank} · {strategy.title}
            {selected ? <span className="ml-2 rounded bg-primary px-2 py-0.5 text-xs text-primary-foreground">selected</span> : null}
          </h3>
        </CardTitle>
        <CardDescription>
          Addresses {templateLabel(strategy.hypothesis.template).toLowerCase()} in{" "}
          <span className="font-mono text-xs">{strategy.incident.key}</span>
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 text-sm">
        <p>{strategy.summary}</p>
        {strategy.rationale_segments.map((segments, i) => (
          <p key={i} className="text-muted-foreground">
            <Segments segments={segments} />
            {strategy.rationale.source === "llm" ? <span className="ml-1 text-xs">(AI-written explanation)</span> : null}
          </p>
        ))}
        {!strategy.eligibility.eligible ? (
          <p role="note">
            Not available:{" "}
            {strategy.eligibility.reasons.map((r) => INELIGIBLE[r] ?? r).join("; ")}.
          </p>
        ) : null}

        <div className="grid gap-1">
          <h4 className="font-medium">Actions it needs</h4>
          <ul className="list-disc pl-5" aria-label="Action types">
            {strategy.action_types.map((a) => (
              <li key={a}>
                {actionLabels[a]?.label ?? a}
                {actionLabels[a]?.risky ? <span className="ml-1 text-xs font-medium">(external effect: needs your explicit permission)</span> : null}
              </li>
            ))}
          </ul>
        </div>

        <div className="grid gap-1">
          <h4 className="font-medium">Measured (from stored evidence)</h4>
          <ul className="grid gap-1" aria-label="Measured inputs">
            {shown.map((o) => (
              <li key={o.name} title={`From evidence ${o.evidence_id} (${o.field})`}>
                {OBSERVED_LABELS[o.name]}: <span className="font-medium tabular-nums">{observedValue(o.unit, o.value, currency)}</span>
              </li>
            ))}
          </ul>
        </div>

        <table className="w-full text-left" aria-label="Assumptions">
          <caption className="text-left font-medium">Assumptions</caption>
          <thead>
            <tr className="border-b">
              <th scope="col" className="py-1 font-medium">Assumption</th>
              <th scope="col" className="py-1 text-right font-medium">Low</th>
              <th scope="col" className="py-1 text-right font-medium">Mid</th>
              <th scope="col" className="py-1 text-right font-medium">High</th>
              <th scope="col" className="py-1 font-medium">Source</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(strategy.assumptions).map(([key, a]) => (
              <tr key={key} className="border-b last:border-0">
                <th scope="row" className="py-1 font-normal">{a.label}</th>
                {(["low", "mid", "high"] as const).map((s) => (
                  <td key={s} className="py-1 text-right tabular-nums">
                    {a.unit === "money" ? formatMoney(a[s], currency) : formatPercent(a[s])}
                  </td>
                ))}
                <td className="py-1">{SOURCE[a.source]}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {estimate ? (
          <table className="w-full text-left" aria-label="Estimate">
            <caption className="text-left font-medium">
              Estimate over the plan horizon (computed from the measured values and assumptions; not an observed result)
            </caption>
            <thead>
              <tr className="border-b">
                <th scope="col" className="py-1 font-medium">Scenario</th>
                <th scope="col" className="py-1 text-right font-medium">Benefit</th>
                <th scope="col" className="py-1 text-right font-medium">Cost</th>
                <th scope="col" className="py-1 text-right font-medium">Net</th>
              </tr>
            </thead>
            <tbody>
              {(["low", "mid", "high"] as const).map((s) => (
                <tr key={s} className="border-b last:border-0">
                  <th scope="row" className="py-1 font-normal capitalize">{s}</th>
                  <td className="py-1 text-right tabular-nums">{formatMoney(estimate.scenarios[s].benefit, currency)}</td>
                  <td className="py-1 text-right tabular-nums">{formatMoney(estimate.scenarios[s].cost, currency)}</td>
                  <td className="py-1 text-right tabular-nums">{formatMoney(estimate.scenarios[s].net, currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-muted-foreground">
            No estimate yet:{" "}
            {strategy.estimate_status === "missing_assumptions"
              ? "it needs a cost from you."
              : strategy.estimate_status === "ineligible"
                ? "the evidence does not support it."
                : "this strategy is not estimated."}
          </p>
        )}
        {estimate?.break_even.variable ? (
          <p>
            Break-even {estimate.break_even.variable.replaceAll("_", " ")}:{" "}
            {estimate.break_even.value !== null && estimate.break_even.reason === null
              ? formatPercent(estimate.break_even.value)
              : BREAK_EVEN[estimate.break_even.reason ?? ""] ?? "not defined"}
          </p>
        ) : null}
        {strategy.targeting ? (
          <p>
            Customers targeted: {strategy.targeting.selected.length} of {strategy.targeting.eligible_customers} eligible (
            {strategy.targeting.source === "human" ? "your selection" : "all eligible, after exclusions"})
            {strategy.discount ? ` · offer discount ${formatPercent(strategy.discount)}` : ""}
          </p>
        ) : null}

        <div className="grid gap-1 sm:grid-cols-2">
          <div>
            <h4 className="font-medium">Operational requirements</h4>
            <ul className="list-disc pl-5">
              {strategy.requirements.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </div>
          <div>
            <h4 className="font-medium">Trade-offs and limits</h4>
            <ul className="list-disc pl-5">
              {strategy.tradeoffs.map((r) => (
                <li key={r}>{r}</li>
              ))}
              {strategy.warnings.map((w) => (
                <li key={w}>{PLAN_ISSUES[w] ?? w}</li>
              ))}
            </ul>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
