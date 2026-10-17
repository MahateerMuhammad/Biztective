"use client";

import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { formatMoney } from "@/lib/format";

export interface StrategyBar {
  name: string;
  benefit: number;
  cost: number;
}

/** Mid-scenario benefit against cost for each estimated strategy. A visual aid only: the
 * comparison table beside it holds every number, including the low and high scenarios. */
export function StrategyChart({ bars, currency }: { bars: StrategyBar[]; currency: string }) {
  const best = [...bars].sort((a, b) => b.benefit - b.cost - (a.benefit - a.cost))[0];
  const summary = best
    ? `Estimated benefit and cost for ${bars.length} strategies, mid scenario. Highest net: ${best.name}, ${formatMoney(best.benefit - best.cost, currency)}.`
    : "No strategy has an estimate yet.";
  return (
    <figure className="grid gap-2">
      <div role="img" aria-label={summary} style={{ height: Math.max(160, bars.length * 44 + 60) }} className="w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={bars} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 8 }}>
            <CartesianGrid stroke="var(--border)" horizontal={false} />
            <XAxis type="number" tick={{ fontSize: 13 }} stroke="var(--muted-foreground)" tickFormatter={(v: number) => formatMoney(v, currency)} />
            <YAxis type="category" dataKey="name" width={170} tick={{ fontSize: 13 }} stroke="var(--muted-foreground)" />
            <Tooltip formatter={(v, n) => [formatMoney(Number(v), currency), String(n)]} />
            <Legend wrapperStyle={{ fontSize: 13 }} />
            <Bar dataKey="benefit" name="Benefit (mid)" fill="var(--action-foreground)" isAnimationActive={false} />
            <Bar dataKey="cost" name="Cost (mid)" fill="var(--hypothesis-foreground)" isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="text-[13px] text-muted-foreground">{summary}</figcaption>
    </figure>
  );
}
