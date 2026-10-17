"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { CheckCircle2, ChevronRight, ClipboardList } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";

import { DraggableWidgetGrid, type WidgetItem } from "@/components/ui/draggable-widget-grid";
import { TREND, type HealthData, type IncidentView } from "@/lib/health";

import { StartInvestigation } from "@/app/(app)/businesses/[businessId]/datasets/[datasetId]/analytics/start-investigation";

import { Button } from "@/components/ui/button";
import { InfoTip } from "@/components/ui/info-tip";
import { Disclosure, EmptyState, PageHeader, StatusChip } from "@/components/page";
import { formatDate } from "@/lib/format";

import { ACCENT, BarChart, Big, Delta, Dot, Shell, Sparkline, linkClass, type Tone } from "./primitives";

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
  { id: "incidents", size: "lg", label: "Biggest changes to look at" },
  { id: "orders", size: "sm", label: "Orders" },
  { id: "aov", size: "sm", label: "Average order value" },
  { id: "active-customers", size: "sm", label: "Active customers" },
  { id: "investigations", size: "sm", label: "Investigations" },
  { id: "weekly-trend", size: "lg", label: "Weekly trend" },
  { id: "customer-segments", size: "lg", label: "Customer segments" },
  { id: "data-quality", size: "wide", label: "Data freshness and quality" },
  { id: "decisions", size: "tall", label: "Decisions" },
];

export const layoutKey = (businessId: string) => `biztective:layout:${businessId}`;
const legacyLayoutKey = (businessId: string) => `root:layout:${businessId}`;

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
    // First read after the rename: keep the layout saved under the old key, once.
    const saved = window.localStorage.getItem(layoutKey(businessId)) ?? window.localStorage.getItem(legacyLayoutKey(businessId));
    return restoreLayout(DEFAULT_WIDGETS, saved);
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

const Empty = ({ children }: { children: React.ReactNode }) => <p className="text-[15px] text-muted-foreground">{children}</p>;

/* Widgets. Each one has a single main number or message and at most two supporting elements;
 * anything else lives in a tooltip, a disclosure or the drill-down page. */

const lastN = <T,>(items: T[], n: number) => items.slice(-n);

const buttonLink =
  "inline-flex h-11 w-fit items-center justify-center rounded-lg bg-primary px-4 text-[15px] font-medium text-primary-foreground hover:bg-primary/85 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring";

function KpiWidget({ data, metric }: { data: HealthData; metric: string }) {
  const k = data.kpis?.[metric];
  const series = data.weekly?.series[metric];
  const n = 12;
  return (
    <Shell title={k?.label ?? metric}>
      <Big title={k && k.short !== k.value ? `Exact value: ${k.value}` : undefined}>{k?.short ?? "—"}</Big>
      <div className="mt-2">{k ? <Delta text={k.change} arrow={k.arrow} outcome={k.outcome} /> : null}</div>
      {series ? (
        <div className="mt-auto pt-3">
          <Sparkline className="h-12" values={lastN(series.values, n)} label={`${series.label}, last ${Math.min(n, series.values.length)} weeks`} />
        </div>
      ) : null}
    </Shell>
  );
}

/** "Net revenue by week, from $A in the week of X to $B in the week of Y." */
function chartSummary(label: string, weeks: string[], text: string[], values: (number | null)[]): string {
  const first = values.findIndex((v) => v !== null);
  if (first < 0) return `${label}: no weekly values.`;
  return `${label} by week, from ${text[first]} in the week of ${formatDate(weeks[first])} to ${text.at(-1)} in the week of ${formatDate(weeks.at(-1))}.`;
}

const SEVERITY_TONE: Record<IncidentView["severity"], Tone> = { critical: "err", high: "err", medium: "warn", low: "idle" };

function Incidents({ data }: { data: HealthData }) {
  const { incidents, analysing, analysisStatus, incidentsTotal } = data;
  return (
    <Shell
      title="Biggest changes to look at"
      meta={
        incidentsTotal > 3 ? (
          <WLink href={data.incidentsHref}>
            View all {incidentsTotal}
          </WLink>
        ) : undefined
      }
      scroll
      scrollLabel="Biggest changes to look at, scrollable"
      footer={incidents.length > 0 ? <p className="text-[13px] text-muted-foreground">Impacts overlap and must not be added together.</p> : undefined}
    >
      {analysing ? <Empty>Looking for changes…</Empty> : null}
      {!analysing && analysisStatus === "insufficient_data" ? <Empty>Not enough weeks of history yet to spot changes reliably.</Empty> : null}
      {!analysing && analysisStatus === "completed" && incidents.length === 0 ? (
        <Empty>No material changes found. Nothing needs investigating right now.</Empty>
      ) : null}
      {incidents.length > 0 ? (
        <ol aria-label="Changes by priority" className="flex flex-1 flex-col justify-between divide-y divide-border">
          {incidents.map((i) => (
            <li key={i.id} className="flex flex-1 items-center py-2 first:pt-0 last:pb-0">
              <WLink
                href={i.href}
                className="group -mx-2 flex min-h-11 w-full items-center gap-3 rounded-lg px-2 py-2 text-foreground outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="grid min-w-0 flex-1 justify-items-start gap-1">
                  <StatusChip tone={SEVERITY_TONE[i.severity]}>{i.severityLabel}</StatusChip>
                  <span className="text-[15px] leading-snug">{i.sentence}</span>
                  <span className="text-[15px] text-muted-foreground" title={`Estimated impact ${i.impactExact}`}>
                    Impact about <span className="font-medium text-foreground tabular-nums">{i.impact}</span>
                  </span>
                </span>
                <ChevronRight aria-hidden="true" className="size-5 shrink-0 text-muted-foreground group-hover:text-foreground" />
              </WLink>
            </li>
          ))}
        </ol>
      ) : null}
    </Shell>
  );
}

function NetRevenue({ data }: { data: HealthData }) {
  const k = data.kpis?.net_revenue;
  const series = data.weekly?.series.net_revenue;
  const weeks = data.weekly?.weeks ?? [];
  const [active, setActive] = useState<number | null>(null);
  return (
    <Shell title={k?.label ?? "Net revenue"}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Big title={k && k.short !== k.value ? `Exact value: ${k.value}` : undefined}>{k?.short ?? "—"}</Big>
        {k ? <Delta text={k.change} arrow={k.arrow} outcome={k.outcome} /> : null}
      </div>
      {series ? (
        <BarChart
          className="mt-4 min-h-[80px] flex-1"
          values={series.values}
          weeks={weeks}
          texts={series.text}
          ends
          interactive
          active={active}
          onActive={setActive}
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
  const [table, setTable] = useState(false);
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
      meta={table ? undefined : `Week of ${formatDate(weekly.weeks[index])}`}
      scroll
      scrollLabel="Weekly trend, scrollable"
    >
      <Big title={series.short[index] !== series.text[index] ? `Exact value: ${series.text[index]}` : undefined}>{series.short[index]}</Big>
      <div role="group" aria-label="Metric shown" className="mt-3 flex flex-wrap gap-1.5">
        {TREND.map((m) => (
          <button
            key={m}
            type="button"
            aria-pressed={m === series.key}
            onClick={() => setKey(m)}
            className="inline-flex min-h-9 items-center rounded-full px-3 text-sm text-muted-foreground ring-1 ring-border ring-inset hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring aria-pressed:bg-primary/10 aria-pressed:font-medium aria-pressed:text-primary aria-pressed:ring-primary/40"
          >
            {weekly.series[m]?.label ?? m}
          </button>
        ))}
      </div>
      {table ? (
        <div tabIndex={0} role="region" aria-label="Weekly numbers" className="mt-4 max-h-72 overflow-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Weekly metrics, whole business</caption>
            <thead className="sticky top-0 bg-card">
              <tr className="border-b border-border">
                <th scope="col" className="py-1.5 font-medium">
                  Week of
                </th>
                {TREND.map((m) => (
                  <th key={m} scope="col" className="py-1.5 text-right font-medium">
                    {weekly.series[m]?.label ?? m}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {weekly.weeks.map((w, i) => (
                <tr key={w} className="border-b border-border last:border-0">
                  <th scope="row" className="py-1.5 font-normal">
                    {formatDate(w)}
                  </th>
                  {TREND.map((m) => (
                    <td key={m} className="py-1.5 text-right tabular-nums">
                      {weekly.series[m]?.text[i]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <BarChart
          className="mt-4 min-h-[130px] flex-1"
          values={series.values}
          weeks={weekly.weeks}
          texts={series.text}
          ends
          interactive
          active={active}
          onActive={setActive}
          label={summary}
        />
      )}
      <p className="sr-only">{summary}</p>
      <button
        type="button"
        aria-pressed={table}
        onClick={() => setTable((t) => !t)}
        className="mt-2 inline-flex min-h-9 w-fit items-center rounded-md px-1 text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring"
      >
        {table ? "Show as chart" : "Show as table"}
      </button>
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
    <Shell title="Customer segments" scroll scrollLabel="Customer segments, scrollable">
      <Big unit="customers">{s.customers}</Big>
      <div
        role="img"
        aria-label={`Share of customers: ${s.rows.map((r) => `${r.label} ${r.shareText}`).join(", ")}.`}
        className="mt-4 flex h-4 gap-[3px]"
      >
        {s.rows.map((r) => (
          <span key={r.key} className={`h-full rounded-full ${swatch(r.key)}`} style={{ width: `${Math.max(1, r.portion * 100)}%` }} />
        ))}
      </div>
      <table className="mt-3 h-full w-full table-fixed text-left text-[15px]">
        <caption className="sr-only">Customers by lifecycle segment, with revenue over the last 365 days</caption>
        <thead className="sr-only">
          <tr>
            <th scope="col">Segment</th>
            <th scope="col">Customers</th>
            <th scope="col">Net revenue (365 days)</th>
          </tr>
        </thead>
        <tbody>
          {s.rows.map((r) => (
            <tr key={r.key} className="border-b border-border last:border-0">
              <th scope="row" className="py-2 pr-3 font-normal text-foreground">
                <span className="flex items-center gap-2.5" title={`${r.shareText} of customers`}>
                  <span aria-hidden="true" className={`size-2.5 shrink-0 rounded-full ${swatch(r.key)}`} />
                  <span>{r.label}</span>
                  <span className="text-muted-foreground tabular-nums">{r.count}</span>
                </span>
              </th>
              <td className="py-2 text-right font-medium text-foreground tabular-nums">{r.revenue}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Shell>
  );
}

function Quality({ data }: { data: HealthData }) {
  const q = data.quality;
  return (
    <Shell title="Data health" scroll scrollLabel="Data health, scrollable">
      <p className="mt-auto flex items-center gap-2.5 text-lg font-semibold text-foreground">
        <Dot tone={q.clean ? "ok" : "warn"} />
        {q.status}
      </p>
      {q.detail ? <p className="mt-1 text-[15px] text-muted-foreground">{q.detail}</p> : null}
      <p className="mt-2 text-[15px] text-muted-foreground">
        {q.orderLines} order lines · {q.covers} · uploaded <span title={q.uploadedExact}>{q.uploaded}</span>
      </p>
      <p className="mt-2 text-[15px]">
        <WLink href={q.reportHref}>
          View report<span className="sr-only"> for data version {data.datasetVersion}</span>
        </WLink>
      </p>
      {q.restrictions.length > 0 ? (
        <Disclosure summary="What this can’t show" className="mb-auto pt-1">
          <ul className="list-disc pl-5">
            {q.restrictions.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </Disclosure>
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
            <WLink href={action.href} className={buttonLink}>
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
        <ul className="grid gap-1">
          {data.investigations.slice(0, 3).map((i) => (
            <li key={i.id}>
              <WLink
                href={i.href}
                className="-mx-2 flex min-h-11 items-center justify-between gap-3 rounded-lg px-2 text-[15px] text-foreground outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="tabular-nums">{i.date}</span>
                <StatusChip tone={STATUS_TONE[i.statusKey] ?? "idle"}>{i.status}</StatusChip>
              </WLink>
            </li>
          ))}
        </ul>
      )}
    </Shell>
  );
}

function Decisions({ data }: { data: HealthData }) {
  return (
    <Shell title="Decisions" scroll scrollLabel="Recovery plans, scrollable">
      {data.plans.length === 0 ? (
        <EmptyState
          icon={ClipboardList}
          title="No recovery plans yet"
          className="flex-1"
          action={
            <WLink href={data.decisionsHref} className={buttonLink}>
              Open Decision center
            </WLink>
          }
        >
          A plan turns what an investigation found into actions you approve.
        </EmptyState>
      ) : (
        <>
          <ul className="grid gap-1">
            {data.plans.map((p) => (
              <li key={p.id}>
                <WLink
                  href={p.href}
                  className="-mx-2 flex min-h-11 items-center justify-between gap-3 rounded-lg px-2 text-[15px] text-foreground outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {p.text}
                  <ChevronRight aria-hidden="true" className="size-5 shrink-0 text-muted-foreground" />
                </WLink>
              </li>
            ))}
          </ul>
          <p className="mt-auto pt-3 text-[15px]">
            <WLink href={data.decisionsHref}>Open Decision center</WLink>
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

const subscribeWidth = (notify: () => void) => {
  window.addEventListener("resize", notify);
  return () => window.removeEventListener("resize", notify);
};

/**
 * Row height as a fraction of column width. On phones one column is wide, so rows are short; on mid
 * screens cells stay square; on large screens they are a little shorter so small tiles are not mostly empty.
 */
function useCellAspect(): number {
  const width = useSyncExternalStore(subscribeWidth, () => window.innerWidth, () => 1440);
  return width < 640 ? 0.85 : width >= 1280 ? 0.72 : 1;
}

export function HealthGrid({ data, subtitle }: { data: HealthData; subtitle: React.ReactNode }) {
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
      window.localStorage.removeItem(legacyLayoutKey(businessId));
    } catch {
      // nothing to clear
    }
    setLayout((l) => ({ items: DEFAULT_WIDGETS, version: l.version + 1 }));
  }

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Business health"
        subtitle={subtitle}
        action={
          <>
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 py-0.5 pr-0.5 pl-3 text-sm font-medium text-emerald-800 dark:text-emerald-300">
              <CheckCircle2 aria-hidden="true" className="size-4" />
              Verified
              <InfoTip label="What does Verified mean?">Every number on this page is measured from your uploaded records.</InfoTip>
            </span>
            <Button variant="outline" onClick={reset}>
              Reset layout
            </Button>
          </>
        }
      />
      <DraggableWidgetGrid
        key={layout.version}
        editable
        cellSize={280}
        minColumns={1}
        maxColumns={3}
        gap={16}
        cellAspect={cellAspect}
        items={layout.items}
        onChange={(items) => save(items)}
        renderItem={(item) => renderWidget(data, item)}
      />
    </div>
  );
}
