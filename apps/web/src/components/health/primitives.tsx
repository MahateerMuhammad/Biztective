import { ArrowDown, ArrowRight, ArrowUp } from "lucide-react";
import type { ReactNode } from "react";

import { formatDate } from "@/lib/format";

/* Building blocks for the Business health widgets. Neutral theme tokens, blue for volume, and
 * status colours only for status. Every status also carries a text label (WCAG 1.4.1). */

export type Tone = "ok" | "warn" | "err" | "idle";

export const DOT: Record<Tone, string> = {
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  err: "bg-rose-500",
  idle: "bg-muted-foreground/60",
};

export const TEXT: Record<Tone, string> = {
  ok: "text-emerald-800 dark:text-emerald-400",
  warn: "text-amber-700 dark:text-amber-300",
  err: "text-rose-700 dark:text-rose-400",
  idle: "text-muted-foreground",
};

export const CHIP: Record<Tone, string> = {
  ok: "bg-emerald-500/10 text-emerald-800 dark:text-emerald-300",
  warn: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
  err: "bg-rose-500/10 text-rose-700 dark:text-rose-300",
  idle: "bg-foreground/[0.06] text-muted-foreground",
};

export const ACCENT = "bg-blue-500 dark:bg-blue-400";

export function Shell({
  title,
  meta,
  scroll = false,
  scrollLabel,
  footer,
  children,
}: {
  title: string;
  meta?: ReactNode;
  /** Let the body scroll inside the tile instead of overflowing it. */
  scroll?: boolean;
  scrollLabel?: string;
  /** Pinned under the body, outside the scrolling part. */
  footer?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="@container flex h-full flex-col gap-3 p-6">
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 leading-snug">
        <h2 className="truncate text-sm font-medium text-muted-foreground">{title}</h2>
        {meta ? <span className="shrink-0 text-[13px] text-muted-foreground">{meta}</span> : null}
      </header>
      {scroll ? (
        <div
          tabIndex={0}
          role="region"
          aria-label={scrollLabel ?? title}
          className="-m-1 flex min-h-0 flex-1 flex-col overflow-y-auto p-1 focus-visible:outline-2 focus-visible:outline-ring"
        >
          {children}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">{children}</div>
      )}
      {footer}
    </section>
  );
}

/** The one number a tile is about. Money arrives already rounded; the exact value can go in `title`. */
export function Big({ children, unit, title }: { children: ReactNode; unit?: string; title?: string }) {
  return (
    <p title={title} className="text-4xl leading-tight font-semibold tracking-tight text-foreground tabular-nums @[360px]:text-[2.5rem]">
      {children}
      {unit ? <span className="text-[15px] font-normal tracking-normal text-muted-foreground"> {unit}</span> : null}
    </p>
  );
}

/** "+3.2%" / "-0.4 pts" as words: "up 3.2%" / "down 0.4 pts". */
export function plainChange(text: string, arrow: "up" | "down" | "flat"): string {
  if (arrow === "flat" || text === "—") return text === "—" ? "No comparison yet" : "No change";
  return `${arrow === "up" ? "Up" : "Down"} ${text.replace(/^[+-]/, "")}`;
}

/** A change against the previous period, said in words, with a coloured arrow. */
export function Delta({
  text,
  arrow,
  outcome,
  against = "vs last period",
}: {
  text: string;
  arrow: "up" | "down" | "flat";
  outcome: "better" | "worse" | "flat";
  against?: string;
}) {
  const tone: Tone = outcome === "better" ? "ok" : outcome === "worse" ? "err" : "idle";
  const Arrow = arrow === "up" ? ArrowUp : arrow === "down" ? ArrowDown : ArrowRight;
  const none = text === "—";
  return (
    <span className={`inline-flex w-fit items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[15px] font-medium tabular-nums ${CHIP[tone]}`}>
      <Arrow aria-hidden="true" className="size-4 shrink-0" strokeWidth={2.5} />
      <span>
        {plainChange(text, arrow)}
        {none ? "" : ` ${against}`}
      </span>
      {none ? null : <span className="sr-only"> ({outcome === "flat" ? "unchanged" : outcome})</span>}
    </span>
  );
}

export function Dot({ tone }: { tone: Tone }) {
  return (
    <span aria-hidden="true" className="relative inline-flex size-2 shrink-0">
      <span className={`relative size-2 rounded-full ${DOT[tone]}`} />
    </span>
  );
}

/** Label on the left, value on the right, one quiet line. */
export function Row({ children, value, className = "" }: { children: ReactNode; value: ReactNode; className?: string }) {
  return (
    <div className={`flex items-center gap-2 text-[15px] ${className}`}>
      <dt className="flex min-w-0 items-center gap-2 truncate text-foreground">{children}</dt>
      <dd className="ml-auto text-muted-foreground tabular-nums">{value}</dd>
    </div>
  );
}

/** A line of the last weeks as a tiny picture, for KPI tiles. Decorative: the numbers are in the tile. */
export function Sparkline({ values, label, className = "" }: { values: (number | null)[]; label: string; className?: string }) {
  const known = values.map((v, i) => (v === null ? null : ([i, v] as const))).filter((p) => p !== null);
  if (known.length < 2) return null;
  const min = Math.min(...known.map((p) => p[1]));
  const max = Math.max(...known.map((p) => p[1]));
  const span = max - min || 1;
  const w = 100;
  const h = 32;
  const pts = known.map(([i, v]) => [(i / (values.length - 1)) * w, h - 3 - ((v - min) / span) * (h - 6)] as const);
  const line = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  return (
    <svg role="img" aria-label={label} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={`${className || "h-8"} w-full overflow-visible`}>
      <polygon points={`0,${h} ${line} ${w},${h}`} className="fill-blue-500/10 dark:fill-blue-400/15" />
      <polyline points={line} fill="none" strokeWidth={2} vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" className="stroke-blue-600 dark:stroke-blue-400" />
    </svg>
  );
}

/**
 * Bar chart for weekly values, pure markup (no chart library). Bars are scaled from just below the
 * minimum, not from zero, so week-to-week differences are visible; the shortest bar is still at least
 * 12% tall. The latest bar (or the inspected one) takes the accent colour.
 *
 * It is a picture of numbers that are also available as text: `label` summarises it for assistive
 * technology and each bar carries a `title`. With `interactive`, a visually hidden range input lets
 * keyboard users step through the weeks, and pointer movement does the same for a mouse.
 */
export function BarChart({
  values,
  weeks,
  texts,
  label,
  ends = false,
  interactive = false,
  active = null,
  onActive,
  className = "",
}: {
  values: (number | null)[];
  weeks?: string[];
  texts?: string[];
  label: string;
  /** Show the first and last week under the bars. */
  ends?: boolean;
  interactive?: boolean;
  active?: number | null;
  onActive?: (index: number | null) => void;
  className?: string;
}) {
  const known = values.filter((v): v is number => v !== null);
  const max = known.length ? Math.max(...known) : 0;
  const min = known.length ? Math.min(...known) : 0;
  const baseline = min - 0.15 * (max - min);
  const height = (v: number | null) =>
    v === null ? 12 : max === min ? 60 : Math.max(12, 12 + 88 * ((v - baseline) / (max - baseline)));
  const highlighted = active ?? values.length - 1;

  return (
    <div className={`relative flex min-h-0 flex-col gap-1.5 rounded-md has-[input:focus-visible]:ring-2 has-[input:focus-visible]:ring-ring has-[input:focus-visible]:ring-offset-2 has-[input:focus-visible]:ring-offset-card ${className}`}>
      <div className="flex min-h-0 flex-1 gap-2">
        <div
          role="img"
          aria-label={label}
          onPointerMove={
            interactive
              ? (e) => {
                  const box = e.currentTarget.getBoundingClientRect();
                  const i = Math.floor(((e.clientX - box.left) / box.width) * values.length);
                  onActive?.(Math.min(values.length - 1, Math.max(0, i)));
                }
              : undefined
          }
          onPointerLeave={interactive ? () => onActive?.(null) : undefined}
          className="relative flex min-h-[48px] flex-1 items-end gap-[3px]"
        >
          {active !== null && weeks && texts ? (
            <span
              aria-hidden="true"
              className="pointer-events-none absolute -top-1 z-10 w-max -translate-y-full rounded-lg bg-popover px-2.5 py-1.5 text-[13px] leading-snug text-popover-foreground shadow-md ring-1 ring-border"
              style={{ left: `${Math.min(88, Math.max(12, ((active + 0.5) / values.length) * 100))}%`, transform: "translate(-50%, -100%)" }}
            >
              Week of {formatDate(weeks[active])}
              <span className="block font-semibold tabular-nums">{texts[active]}</span>
            </span>
          ) : null}
          {values.map((v, i) => (
            <span
              key={i}
              title={weeks && texts ? `Week of ${formatDate(weeks[i])}: ${texts[i]}` : undefined}
              className={`flex-1 rounded-[3px] transition-[height] duration-500 motion-reduce:transition-none ${
                i === highlighted ? ACCENT : "bg-foreground/15"
              }`}
              style={{ height: `${height(v)}%` }}
            />
          ))}
        </div>
      </div>
      {ends && weeks && weeks.length > 1 ? (
        <div aria-hidden="true" className="flex justify-between text-[13px] text-muted-foreground">
          <span>{formatDate(weeks[0])}</span>
          <span>{formatDate(weeks[weeks.length - 1])}</span>
        </div>
      ) : null}
      {interactive ? (
        <input
          type="range"
          min={0}
          max={Math.max(0, values.length - 1)}
          value={highlighted}
          aria-label="Inspect a week"
          aria-valuetext={weeks && texts ? `Week of ${formatDate(weeks[highlighted])}: ${texts[highlighted]}` : undefined}
          onChange={(e) => onActive?.(Number(e.target.value))}
          onFocus={() => onActive?.(values.length - 1)}
          onBlur={() => onActive?.(null)}
          className="pointer-events-none absolute inset-0 size-full opacity-0"
        />
      ) : null}
    </div>
  );
}

export const linkClass =
  "font-medium text-foreground underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring";
