"use client";

import { useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { METRIC_LABELS, formatMetric } from "@/lib/format";

export interface TrendPoint {
  week: string;
  values: Record<string, string | number | null>;
}

/**
 * Weekly trend of one metric at a time. The chart is a visual aid only: the same numbers are
 * in the table next to it (WCAG 1.1.1), and the metric is chosen with real buttons.
 */
export function TrendChart({ points, metrics, currency }: { points: TrendPoint[]; metrics: string[]; currency: string }) {
  const [metric, setMetric] = useState(metrics[0] ?? "net_revenue");
  const data = points.map((p) => ({ week: p.week, value: p.values[metric] === null ? null : Number(p.values[metric]) }));
  const label = METRIC_LABELS[metric] ?? metric;
  const shown = data.filter((d) => d.value !== null);
  const first = shown[0];
  const last = shown.at(-1);
  const summary =
    first && last
      ? `${label} by week, from ${formatMetric(metric, first.value, currency)} in the week of ${first.week} to ${formatMetric(metric, last.value, currency)} in the week of ${last.week}.`
      : `${label}: no weekly values.`;
  return (
    <figure className="grid gap-3">
      <div role="group" aria-label="Metric shown in the chart" className="flex flex-wrap gap-1">
        {metrics.map((m) => (
          <button
            key={m}
            type="button"
            aria-pressed={m === metric}
            onClick={() => setMetric(m)}
            className="rounded-md border px-2.5 py-1 text-[13px] font-medium hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring aria-pressed:border-foreground aria-pressed:bg-foreground aria-pressed:text-background"
          >
            {METRIC_LABELS[m] ?? m}
          </button>
        ))}
      </div>
      <div role="img" aria-label={summary} className="h-56 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
            <CartesianGrid stroke="var(--border)" vertical={false} />
            <XAxis dataKey="week" tickFormatter={(w: string) => w.slice(5)} tick={{ fontSize: 13 }} stroke="var(--muted-foreground)" minTickGap={24} />
            <YAxis
              width={72}
              tick={{ fontSize: 13 }}
              stroke="var(--muted-foreground)"
              tickFormatter={(v: number) => formatMetric(metric, v, currency)}
            />
            <Tooltip
              formatter={(v) => [formatMetric(metric, Number(v), currency), label]}
              labelFormatter={(w) => `Week of ${String(w)}`}
            />
            <Line type="monotone" dataKey="value" stroke="var(--fact-foreground)" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="text-[13px] text-muted-foreground">{summary}</figcaption>
    </figure>
  );
}
