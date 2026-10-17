"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ClipboardList } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";

import { DraggableWidgetGrid, type WidgetItem } from "@/components/ui/draggable-widget-grid";
import { TREND, type HealthData, type IncidentView } from "@/lib/health";

import { StartInvestigation } from "@/app/(app)/businesses/[businessId]/datasets/[datasetId]/analytics/start-investigation";

import { ACCENT, BarChart, Big, CHIP, DOT, Delta, Dot, Shell, TEXT, linkClass, type Tone } from "./primitives";

/**
 * Sizes total 24 cells (sm 1, wide 2, tall 2, lg 4), a multiple of 12, so the grid can tile exactly at
 * 2, 3 and 4 columns. The mix is forced: with three large tiles, a third wide tile would make nine
 * two-column row slices, more than the eight rows of a 3-column grid, which no order can tile. So it is
 * three large, two wide, one tall and six small (tests/health-grid.test.tsx checks the tiling).
 */
export const DEFAULT_WIDGETS: WidgetItem[] = [
  { id: "net-revenue", size: "wide", label: "Net revenue" },
  { id: "gross-margin", size: "sm", label: "Gross margin" },
  { id: "refund-rate", size: "sm", label: "Refund rate" },
  { id: "incidents", size: "lg", label: "Prioritized incidents" },
  { id: "orders", size: "sm", label: "Orders" },
  { id: "aov", size: "sm", label: "Average order value" },
  { id: "active-customers", size: "sm", label: "Active customers" },
  { id: "investigations", size: "sm", label: "Investigations" },
  { id: "weekly-trend", size: "lg", label: "Weekly trend" },
  { id: "customer-segments", size: "lg", label: "Customer segments" },
  { id: "data-quality", size: "wide", label: "Data freshness and quality" },
  { id: "decisions", size: "tall", label: "Decisions" },
];

export const layoutKey = (businessId: string) => `root:layout:${businessId}`;

/** Applies a saved order of ids to the defaults. Unknown ids are dropped; new widgets keep their default place at the end. */
export function restoreLayout(defaults: WidgetItem[], raw: string | null): WidgetItem[] {
  if (!raw) return defaults;
  try {
    const saved: unknown = JSON.parse(raw);
    if (!Array.isArray(saved)) return defaults;
    const byId = new Map(defaults.map((d) => [d.id, d]));
    const order: WidgetItem[] = [];
    for (const id of saved) {
      const item = typeof id === "string" ? byId.get(id) : undefined;
      if (item && !order.includes(item)) order.push(item);
    }
    if (order.length === 0) return defaults;
    return [...order, ...defaults.filter((d) => !order.includes(d))];
  } catch {
    return defaults;
  }
}

function readLayout(businessId: string): WidgetItem[] {
  try {
    return restoreLayout(DEFAULT_WIDGETS, window.localStorage.getItem(layoutKey(businessId)));
  } catch {
    return DEFAULT_WIDGETS;
  }
}

function WLink({ href, className = linkClass, children }: { href: string; className?: string; children: React.ReactNode }) {
  return (
    <Link href={href} data-widget-link className={className}>
      {children}
    </Link>
  );
}

const Empty = ({ children }: { children: React.ReactNode }) => <p className="text-[13px] text-muted-foreground">{children}</p>;

/* Widgets */

const lastN = <T,>(items: T[], n: number) => items.slice(-n);

function KpiWidget({ data, metric }: { data: HealthData; metric: string }) {
  const k = data.kpis?.[metric];
  const series = data.weekly?.series[metric];
  const weeks = data.weekly?.weeks ?? [];
  const previous = data.previous[metric];
  const n = 12;
  return (
    <Shell title={k?.label ?? metric}>
      <Big>{k?.value ?? "—"}</Big>
      <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
        {k ? <Delta text={k.change} arrow={k.arrow} outcome={k.outcome} /> : null}
        {previous ? <span className="font-mono text-[11px] text-muted-foreground">Previous period: {previous}</span> : null}
      </div>
      {series ? (
        <BarChart
          className="mt-3 flex-1"
          values={lastN(series.values, n)}
          weeks={lastN(weeks, n)}
          texts={lastN(series.text, n)}
          label={chartSummary(series.label, lastN(weeks, n), lastN(series.text, n), lastN(series.values, n))}
        />
      ) : null}
    </Shell>
  );
}

/** "Net revenue by week, from $A in the week of X to $B in the week of Y." */
function chartSummary(label: string, weeks: string[], text: string[], values: (number | null)[]): string {
  const first = values.findIndex((v) => v !== null);
  if (first < 0) return `${label}: no weekly values.`;
  return `${label} by week, from ${text[first]} in the week of ${weeks[first]} to ${text.at(-1)} in the week of ${weeks.at(-1)}.`;
}

const SEVERITY_TONE: Record<IncidentView["severity"], Tone> = { critical: "err", high: "err", medium: "warn", low: "idle" };

function Incidents({ data }: { data: HealthData }) {
  const { incidents, analysing, analysisStatus } = data;
  return (
    <Shell
      title="Prioritized incidents"
      meta="last 4 weeks"
      scroll
      scrollLabel="Prioritized incidents, scrollable"
      footer={<p className="text-[12px] text-muted-foreground">Impacts overlap and must not be added together.</p>}
    >
      {analysing ? <Empty>Detection is running…</Empty> : null}
      {!analysing && analysisStatus === "insufficient_data" ? (
        <Empty>Not enough weeks of history yet to detect incidents reliably.</Empty>
      ) : null}
      {!analysing && analysisStatus === "completed" && incidents.length === 0 ? (
        <Empty>No material anomalies were found. Nothing needs investigating right now.</Empty>
      ) : null}
      {incidents.length > 0 ? (
        <ol aria-label="Incidents by priority" className="flex flex-1 flex-col justify-between gap-3">
          {incidents.map((i) => {
            const tone = SEVERITY_TONE[i.severity];
            return (
              <li key={i.id} className="grid gap-1 border-b border-border pb-3 text-[13px] last:border-0 last:pb-0">
                <p className="flex items-start gap-2 text-foreground">
                  <span className="mt-[5px]">
                    <Dot tone={tone} />
                  </span>
                  <span>
                    <span className="font-mono text-muted-foreground tabular-nums">#{i.rank}</span>{" "}
                    <span className={`font-mono text-[11px] tracking-wide uppercase ${TEXT[tone]}`}>{i.severity}</span> {i.title}
                  </span>
                </p>
                <p className="pl-4 text-muted-foreground">
                  Now <span className="font-mono tabular-nums">{i.now}</span> vs usual <span className="font-mono tabular-nums">{i.usual}</span>
                </p>
                <p className="flex flex-wrap items-center gap-x-3 pl-4 text-muted-foreground">
                  <span>
                    Estimated impact <span className="font-mono text-foreground tabular-nums">{i.impact}</span>
                  </span>
                  {i.since ? <span>since week of {i.since}</span> : null}
                  <WLink href={i.href} className={`${linkClass} ml-auto`}>
                    Drill down<span className="sr-only"> into incident {i.rank}</span>
                  </WLink>
                </p>
                <span aria-hidden="true" className="ml-4 block h-[3px] rounded-full bg-foreground/10">
                  <span className={`block h-full rounded-full ${DOT[tone]}`} style={{ width: `${Math.max(3, Math.round(i.impactShare * 100))}%` }} />
                </span>
              </li>
            );
          })}
        </ol>
      ) : null}
    </Shell>
  );
}

function ExtremeChip({ title, text, week }: { title: string; text: string; week: string }) {
  return (
    <span className="rounded-lg bg-foreground/[0.05] px-2.5 py-1.5 text-[11px] leading-tight text-muted-foreground">
      {title}
      <span className="block font-mono text-[13px] text-foreground tabular-nums">{text}</span>
      <span className="block font-mono">{week}</span>
    </span>
  );
}

function NetRevenue({ data }: { data: HealthData }) {
  const k = data.kpis?.net_revenue;
  const series = data.weekly?.series.net_revenue;
  const weeks = data.weekly?.weeks ?? [];
  const previous = data.previous.net_revenue;
  let best = -1;
  let worst = -1;
  series?.values.forEach((v, i) => {
    if (v === null) return;
    if (best < 0 || v > (series.values[best] ?? -Infinity)) best = i;
    if (worst < 0 || v < (series.values[worst] ?? Infinity)) worst = i;
  });
  return (
    <Shell title={k?.label ?? "Net revenue"} meta={series ? `last ${weeks.length} weeks` : undefined}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <Big>{k?.value ?? "—"}</Big>
          <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            {k ? <Delta text={k.change} arrow={k.arrow} outcome={k.outcome} /> : null}
            {previous ? <span className="font-mono text-[11px] text-muted-foreground">Previous period: {previous}</span> : null}
          </div>
        </div>
        {series && best >= 0 ? (
          <div className="hidden gap-2 @[420px]:flex">
            <ExtremeChip title="Best week" text={series.text[best] ?? "—"} week={weeks[best] ?? ""} />
            <ExtremeChip title="Worst week" text={series.text[worst] ?? "—"} week={weeks[worst] ?? ""} />
          </div>
        ) : null}
      </div>
      {series ? (
        <BarChart
          className="mt-3 min-h-[96px] flex-1"
          values={series.values}
          weeks={weeks}
          texts={series.text}
          axis
          ends
          label={`Net revenue over the last ${weeks.length} weeks, as bars. The last bar is the latest week.`}
        />
      ) : (
        <Empty>Not enough weeks of data yet to chart.</Empty>
      )}
    </Shell>
  );
}

function WeeklyTrend({ data }: { data: HealthData }) {
  const weekly = data.weekly;
  const [key, setKey] = useState("net_revenue");
  const [active, setActive] = useState<number | null>(null);
  const series = weekly ? (weekly.series[key] ?? weekly.series.net_revenue) : undefined;
  if (!weekly || !series) {
    return (
      <Shell title="Weekly trend">
        <Empty>Not enough weeks of data yet to show a trend.</Empty>
      </Shell>
    );
  }
  const index = active ?? series.values.length - 1;
  const summary = chartSummary(series.label, weekly.weeks, series.text, series.values);
  return (
    <Shell
      title="Weekly trend"
      meta={active !== null ? `Week of ${weekly.weeks[index]} · ${series.text[index]}` : `last ${weekly.weeks.length} weeks`}
      scroll
      scrollLabel="Weekly trend, scrollable"
    >
      <Big>{series.text[index]}</Big>
      <p className="mt-1 text-[12px] text-muted-foreground">
        {series.label}, week of {weekly.weeks[index]}
      </p>
      <div role="group" aria-label="Metric shown in the chart" className="mt-3 flex flex-wrap gap-1">
        {TREND.map((m) => (
          <button
            key={m}
            type="button"
            aria-pressed={m === series.key}
            onClick={() => setKey(m)}
            className="rounded-full px-2.5 py-1 text-[12px] text-muted-foreground ring-1 ring-border ring-inset hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring aria-pressed:bg-foreground aria-pressed:text-background aria-pressed:ring-foreground"
          >
            {weekly.series[m]?.label ?? m}
          </button>
        ))}
      </div>
      <BarChart
        className="mt-4 min-h-[150px] flex-1"
        values={series.values}
        weeks={weekly.weeks}
        texts={series.text}
        axis
        ends
        interactive
        active={active}
        onActive={setActive}
        label={summary}
      />
      <p className="mt-2 text-[11px] text-muted-foreground">{summary}</p>
      <details className="mt-2 text-[13px]">
        <summary className="cursor-pointer text-foreground underline-offset-4 hover:underline">Show the weekly numbers as a table</summary>
        <div tabIndex={0} role="region" aria-label="Weekly numbers" className="mt-2 max-h-64 overflow-auto">
          <table className="w-full text-left font-mono text-[12px]">
            <caption className="sr-only">Weekly metrics, whole business</caption>
            <thead>
              <tr className="border-b border-border">
                <th scope="col" className="py-1 font-medium">
                  Week of
                </th>
                {TREND.map((m) => (
                  <th key={m} scope="col" className="py-1 text-right font-medium">
                    {weekly.series[m]?.label ?? m}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {weekly.weeks.map((w, i) => (
                <tr key={w} className="border-b border-border last:border-0">
                  <th scope="row" className="py-1 font-normal">
                    {w}
                  </th>
                  {TREND.map((m) => (
                    <td key={m} className="py-1 text-right tabular-nums">
                      {weekly.series[m]?.text[i]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </Shell>
  );
}

const SWATCH = [ACCENT, "bg-blue-500/55 dark:bg-blue-400/55", "bg-blue-500/30 dark:bg-blue-400/30", "bg-foreground/20"];

function Segments({ data }: { data: HealthData }) {
  const s = data.segments;
  if (!s) {
    return (
      <Shell title="Customer segments">
        <Empty>Customer segments are not available yet.</Empty>
      </Shell>
    );
  }
  const rank = new Map([...s.rows].sort((a, b) => b.portion - a.portion).map((r, i) => [r.key, i]));
  const swatch = (key: string) => SWATCH[Math.min(rank.get(key) ?? 3, 3)];
  return (
    <Shell title="Customer segments" meta="365 days" scroll scrollLabel="Customer segments, scrollable">
      <Big>{s.customers}</Big>
      <p className="mt-1 text-[12px] text-muted-foreground">
        customers by buying pattern{s.repeatRate ? ` · repeat rate ${s.repeatRate}` : ""}
      </p>
      <div
        role="img"
        aria-label={`Share of customers: ${s.rows.map((r) => `${r.label} ${r.shareText}`).join(", ")}.`}
        className="mt-4 flex h-2 gap-[3px]"
      >
        {s.rows.map((r) => (
          <span key={r.key} className={`h-full rounded-full ${swatch(r.key)}`} style={{ width: `${Math.max(1, r.portion * 100)}%` }} />
        ))}
      </div>
      <div className="mt-3 flex-1">
        <table className="h-full w-full table-fixed text-left text-[13px]">
          <caption className="sr-only">Customers by lifecycle segment</caption>
          <thead className="sr-only">
            <tr>
              <th scope="col">Segment</th>
              <th scope="col">Share of customers</th>
              <th scope="col">Net revenue (365 days)</th>
            </tr>
          </thead>
          <tbody>
            {s.rows.map((r) => (
              <tr key={r.key} className="align-middle">
                <th scope="row" className="w-[55%] py-1.5 pr-3 font-normal text-foreground">
                  <span className="flex items-center gap-2">
                    <span aria-hidden="true" className={`size-2 shrink-0 rounded-full ${swatch(r.key)}`} />
                    <span className="truncate">{r.label}</span>
                  </span>
                  <span className="mt-0.5 block pl-4 font-mono text-[11px] text-muted-foreground tabular-nums">
                    {r.shareText} ({r.count})
                  </span>
                  <span aria-hidden="true" className="mt-1 ml-4 block h-1 rounded-full bg-foreground/10">
                    <span
                      className={`block h-full rounded-full transition-[width] duration-700 motion-reduce:transition-none ${
                        rank.get(r.key) === 0 ? ACCENT : "bg-muted-foreground/25"
                      }`}
                      style={{ width: `${Math.round(r.share * 100)}%` }}
                    />
                  </span>
                </th>
                <td className="py-1.5 text-right font-mono text-muted-foreground tabular-nums">{r.revenue}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Shell>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-0.5">
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd className="font-mono text-[13px] text-foreground tabular-nums">{children}</dd>
    </div>
  );
}

function Quality({ data }: { data: HealthData }) {
  const q = data.quality;
  return (
    <Shell
      title="Data freshness and quality"
      meta={<WLink href={q.reportHref}>Quality report for dataset v{data.datasetVersion}</WLink>}
      scroll
      scrollLabel="Data freshness and quality, scrollable"
    >
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3">
        <Fact label="Covers">{q.covers}</Fact>
        <Fact label="Uploaded">{q.uploaded}</Fact>
        <Fact label="Order lines">{q.orderLines}</Fact>
        <Fact label="Quality">
          <span className="inline-flex items-center gap-2">
            <Dot tone={q.clean ? "ok" : "warn"} />
            {q.status}
          </span>
        </Fact>
      </dl>
      {q.restrictions.length > 0 ? (
        <div className="mt-auto pt-3 text-[12px] text-muted-foreground">
          <p className="text-foreground">Limits on what the data can show</p>
          <ul className="mt-1 list-disc pl-4">
            {q.restrictions.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </Shell>
  );
}

const STATUS_TONE: Record<string, Tone> = { awaiting_human: "warn", completed: "ok", failed: "err" };

function Investigations({ data }: { data: HealthData }) {
  const action = data.investigationAction;
  return (
    <Shell
      title="Investigations"
      meta={<WLink href={data.investigationsHref}>View all</WLink>}
      scroll
      scrollLabel="Recent investigations, scrollable"
      footer={
        action ? (
          action.kind === "link" ? (
            <WLink
              href={action.href}
              className="inline-flex w-fit rounded-lg bg-foreground px-3 py-1.5 text-[13px] font-medium text-background hover:bg-foreground/85 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              {action.label}
            </WLink>
          ) : (
            <StartInvestigation businessId={data.businessId} datasetId={data.datasetId} />
          )
        ) : null
      }
    >
      {data.investigations.length === 0 ? (
        <Empty>None yet.</Empty>
      ) : (
        <ul className="grid gap-1.5">
          {data.investigations.slice(0, 3).map((i) => {
            const tone = STATUS_TONE[i.statusKey] ?? "idle";
            return (
              <li key={i.id} className="flex items-center justify-between gap-3 text-[13px]">
                <WLink href={i.href} className={`${linkClass} font-mono tabular-nums`}>
                  {i.date}
                </WLink>
                <span className={`rounded-full px-2 py-0.5 text-[11px] ${CHIP[tone]}`}>{i.status}</span>
              </li>
            );
          })}
        </ul>
      )}
    </Shell>
  );
}

function Decisions({ data }: { data: HealthData }) {
  const button =
    "inline-flex rounded-lg bg-foreground px-3 py-1.5 text-[13px] font-medium text-background hover:bg-foreground/85 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring";
  return (
    <Shell title="Decisions" scroll scrollLabel="Recovery plans, scrollable">
      {data.plans.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center">
          <ClipboardList aria-hidden="true" className="size-8 text-muted-foreground" strokeWidth={1.5} />
          <p className="text-sm font-medium text-foreground">No recovery plans yet</p>
          <p className="max-w-[24ch] text-[12px] text-muted-foreground">
            A recovery plan turns what an investigation found into actions you approve.
          </p>
          <WLink href={data.decisionsHref} className={button}>
            Open the Decision center
          </WLink>
        </div>
      ) : (
        <>
          <ul className="grid gap-2 text-[13px]">
            {data.plans.map((p) => (
              <li key={p.id}>
                <WLink href={p.href}>{p.text}</WLink>
              </li>
            ))}
          </ul>
          <p className="mt-auto pt-3 text-[13px]">
            <WLink href={data.decisionsHref}>Open the Decision center</WLink>
          </p>
        </>
      )}
    </Shell>
  );
}

function renderWidget(data: HealthData, item: WidgetItem) {
  switch (item.id) {
    case "net-revenue":
      return <NetRevenue data={data} />;
    case "gross-margin":
      return <KpiWidget data={data} metric="gross_margin_pct" />;
    case "refund-rate":
      return <KpiWidget data={data} metric="refund_rate" />;
    case "orders":
      return <KpiWidget data={data} metric="orders" />;
    case "aov":
      return <KpiWidget data={data} metric="aov" />;
    case "active-customers":
      return <KpiWidget data={data} metric="active_customers" />;
    case "incidents":
      return <Incidents data={data} />;
    case "weekly-trend":
      return <WeeklyTrend data={data} />;
    case "customer-segments":
      return <Segments data={data} />;
    case "data-quality":
      return <Quality data={data} />;
    case "investigations":
      return <Investigations data={data} />;
    case "decisions":
      return <Decisions data={data} />;
    default:
      return null;
  }
}

/**
 * The grid wraps each widget in a handler that cancels clicks on links while it is editable
 * (so a drop never follows a link). To keep links usable, plain clicks on a widget link are
 * handled before that handler sees them: a click that was not a drag navigates, and modified
 * clicks (new tab, new window) are left to the browser.
 */
function useWidgetLinks() {
  const router = useRouter();
  useEffect(() => {
    let down: { x: number; y: number } | null = null;
    let moved = false;
    const onDown = (e: PointerEvent) => {
      down = { x: e.clientX, y: e.clientY };
      moved = false;
    };
    const onMove = (e: PointerEvent) => {
      if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6) moved = true;
    };
    const onClick = (e: MouseEvent) => {
      const anchor = (e.target as Element | null)?.closest?.("a[data-widget-link]") as HTMLAnchorElement | null;
      if (!anchor) return;
      e.stopPropagation();
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return; // browser default
      e.preventDefault();
      if (!moved) router.push(anchor.getAttribute("href") ?? "/");
    };
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("pointermove", onMove, true);
    document.addEventListener("click", onClick, true);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("pointermove", onMove, true);
      document.removeEventListener("click", onClick, true);
    };
  }, [router]);
}

const subscribeWide = (notify: () => void) => {
  const query = window.matchMedia("(min-width: 1280px)");
  query.addEventListener("change", notify);
  return () => query.removeEventListener("change", notify);
};

/** Cells are shorter than they are wide on large screens, so small tiles are not mostly empty; elsewhere they stay square. */
function useCellAspect(): number {
  const wide = useSyncExternalStore(
    subscribeWide,
    () => window.matchMedia("(min-width: 1280px)").matches,
    () => true,
  );
  return wide ? 0.75 : 1;
}

export function HealthGrid({ data }: { data: HealthData }) {
  const { businessId } = data;
  const cellAspect = useCellAspect();
  const [layout, setLayout] = useState<{ items: WidgetItem[]; version: number }>({ items: DEFAULT_WIDGETS, version: 0 });
  useWidgetLinks();

  // The saved order is only known in the browser. The grid takes its order at mount, so a different
  // saved order replaces the default one by remounting the grid once, right after hydration.
  useEffect(() => {
    const saved = readLayout(businessId);
    if (saved.some((item, i) => item.id !== DEFAULT_WIDGETS[i]?.id)) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- browser-only storage, read once after hydration
      setLayout({ items: saved, version: 1 });
    }
  }, [businessId]);

  function save(items: WidgetItem[]) {
    try {
      window.localStorage.setItem(layoutKey(businessId), JSON.stringify(items.map((i) => i.id)));
    } catch {
      // Private mode or storage disabled: the layout simply is not remembered.
    }
  }

  function reset() {
    try {
      window.localStorage.removeItem(layoutKey(businessId));
    } catch {
      // nothing to clear
    }
    setLayout((l) => ({ items: DEFAULT_WIDGETS, version: l.version + 1 }));
  }

  return (
    <div className="grid gap-3">
      <p className="flex flex-wrap items-center justify-between gap-2 text-[12px] text-muted-foreground">
        <span>
          <span className="pointer-coarse:hidden">Drag to rearrange. Hold Alt + arrow keys on a focused widget to move it.</span>
          <span className="hidden pointer-coarse:inline">Press and hold a widget, then drag.</span>
        </span>
        <button
          type="button"
          onClick={reset}
          className="rounded-md px-1.5 py-0.5 underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring"
        >
          Reset layout
        </button>
      </p>
      <DraggableWidgetGrid
        key={layout.version}
        cellAspect={cellAspect}
        items={layout.items}
        onChange={(items) => save(items)}
        renderItem={(item) => renderWidget(data, item)}
      />
    </div>
  );
}
