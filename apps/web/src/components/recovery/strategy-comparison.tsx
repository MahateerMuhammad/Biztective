import { StrategyChart } from "@/components/charts/strategy-chart";
import { KindBadge } from "@/components/kind-badge";
import { Badge } from "@/components/ui/badge";
import type { PlanContent } from "@/lib/api/types";
import { formatMoney } from "@/lib/format";

const STATUS: Record<string, string> = {
  missing_assumptions: "Needs your cost inputs",
  ineligible: "Not applicable here",
  not_estimated: "Not estimated",
};

/** Every competing strategy side by side: estimates (low / mid / high), actions and trade-offs. */
export function StrategyComparison({ content }: { content: PlanContent }) {
  const c = content.currency;
  const estimated = content.strategies.filter((s) => s.estimate);
  const bars = estimated.map((s) => ({
    name: s.title,
    benefit: Number(s.estimate!.scenarios.mid.benefit),
    cost: Number(s.estimate!.scenarios.mid.cost),
  }));
  return (
    <div className="grid gap-4">
      {bars.length > 0 ? <StrategyChart bars={bars} currency={c} /> : null}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Competing strategies compared</caption>
          <thead>
            <tr className="border-b align-bottom">
              <th scope="col" className="py-2 pr-3 font-medium">Strategy</th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">
                Benefit <span className="font-normal text-muted-foreground">(low–high)</span>
              </th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">Cost (mid)</th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">Net (mid)</th>
              <th scope="col" className="py-2 pr-3 font-medium">Actions</th>
              <th scope="col" className="py-2 font-medium">Main trade-off</th>
            </tr>
          </thead>
          <tbody>
            {content.strategies.map((s) => {
              const e = s.estimate?.scenarios;
              const chosen = content.selected.includes(s.id);
              return (
                <tr key={s.id} className="border-b align-top last:border-0">
                  <th scope="row" className="py-2 pr-3 font-normal">
                    <span className="font-medium">{s.title}</span>
                    {chosen ? <Badge className="ml-2 bg-foreground text-background">Selected</Badge> : null}
                    <span className="block tabular-nums text-[13px] text-muted-foreground">{s.incident.key}</span>
                  </th>
                  {e ? (
                    <>
                      <td className="py-2 pr-3 text-right tabular-nums">
                        {formatMoney(e.mid.benefit, c)}
                        <span className="block text-[13px] text-muted-foreground">
                          {formatMoney(e.low.benefit, c)}–{formatMoney(e.high.benefit, c)}
                        </span>
                      </td>
                      <td className="py-2 pr-3 text-right tabular-nums">{formatMoney(e.mid.cost, c)}</td>
                      <td className="py-2 pr-3 text-right font-medium tabular-nums">{formatMoney(e.mid.net, c)}</td>
                    </>
                  ) : (
                    <td colSpan={3} className="py-2 pr-3 text-right text-muted-foreground">
                      {STATUS[s.estimate_status] ?? s.estimate_status}
                    </td>
                  )}
                  <td className="py-2 pr-3">
                    <span className="flex flex-wrap gap-1">
                      {s.action_types.map((a) => (
                        <Badge
                          key={a}
                          className={s.risky_action_types.includes(a) ? "border-risk-foreground/30 bg-risk text-risk-foreground" : "bg-muted"}
                        >
                          {a.replaceAll("_", " ")}
                          {s.risky_action_types.includes(a) ? " (risky)" : ""}
                        </Badge>
                      ))}
                    </span>
                  </td>
                  <td className="py-2 text-[13px] text-muted-foreground">{s.tradeoffs[0] ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="flex flex-wrap items-center gap-2 text-[13px] text-muted-foreground">
        <KindBadge kind="estimate" /> Benefits and costs come from your assumptions applied to measured evidence; they are not
        results.
      </p>
    </div>
  );
}
