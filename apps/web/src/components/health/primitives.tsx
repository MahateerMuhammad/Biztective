import type { ReactNode } from "react";

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
  ok: "text-emerald-700 dark:text-emerald-400",
  warn: "text-amber-700 dark:text-amber-300",
  err: "text-rose-700 dark:text-rose-400",
  idle: "text-muted-foreground",
};

export const CHIP: Record<Tone, string> = {
  ok: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
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
    <section className="@container flex h-full flex-col gap-4 p-4 sm:p-5">
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 text-[14px] leading-none">
        <h2 className="truncate font-mono text-[11px] tracking-[0.1em] text-muted-foreground uppercase">{title}</h2>
        {meta ? <span className="shrink-0 text-[12px] text-muted-foreground">{meta}</span> : null}
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

export function Big({ children, unit }: { children: ReactNode; unit?: string }) {
  return (
    <p className="font-mono text-[28px] leading-none font-normal tracking-tight text-foreground tabular-nums @[240px]:text-[30px]">
      {children}
      {unit ? <span className="text-[13px] tracking-normal text-muted-foreground"> {unit}</span> : null}
    </p>
  );
}

/** A change against the previous period, said in words as well as an arrow. */
export function Delta({
  text,
  arrow,
  outcome,
  against = "compared with previous period",
}: {
  text: string;
  arrow: "up" | "down" | "flat";
  outcome: "better" | "worse" | "flat";
  against?: string;
}) {
  const tone: Tone = outcome === "better" ? "ok" : outcome === "worse" ? "err" : "idle";
  return (
    <span className={`font-mono text-[13px] tabular-nums ${TEXT[tone]}`}>
      <span aria-hidden="true">{arrow === "up" ? "↑" : arrow === "down" ? "↓" : "→"} </span>
      {text}
      <span className="sr-only">
        {" "}
        {against} ({outcome === "flat" ? "unchanged" : outcome})
      </span>
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
    <div className={`flex items-center gap-2 text-[13px] ${className}`}>
      <dt className="flex min-w-0 items-center gap-2 truncate text-foreground">{children}</dt>
      <dd className="ml-auto font-mono text-muted-foreground tabular-nums">{value}</dd>
    </div>
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
  axis = false,
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
  /** Show the highest and lowest value to the left of the bars. */
  axis?: boolean;
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
  const maxText = texts?.[values.findIndex((v) => v === max)];
  const minText = texts?.[values.findIndex((v) => v === min)];

  return (
    <div className={`relative flex min-h-0 flex-col gap-1.5 rounded-md has-[input:focus-visible]:ring-2 has-[input:focus-visible]:ring-ring has-[input:focus-visible]:ring-offset-2 has-[input:focus-visible]:ring-offset-card ${className}`}>
      <div className="flex min-h-0 flex-1 gap-2">
        {axis && known.length > 0 ? (
          <div aria-hidden="true" className="flex w-[4.5em] shrink-0 flex-col justify-between text-right font-mono text-[10px] text-muted-foreground tabular-nums">
            <span>{maxText}</span>
            <span>{minText}</span>
          </div>
        ) : null}
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
          className="flex min-h-[48px] flex-1 items-end gap-[3px]"
        >
          {values.map((v, i) => (
            <span
              key={i}
              title={weeks && texts ? `Week of ${weeks[i]}: ${texts[i]}` : undefined}
              className={`flex-1 rounded-[3px] transition-[height] duration-500 motion-reduce:transition-none ${
                i === highlighted ? ACCENT : "bg-foreground/15"
              }`}
              style={{ height: `${height(v)}%` }}
            />
          ))}
        </div>
      </div>
      {ends && weeks && weeks.length > 1 ? (
        <div aria-hidden="true" className={`flex justify-between font-mono text-[10px] text-muted-foreground ${axis ? "pl-[calc(4.5em+0.5rem)]" : ""}`}>
          <span>{weeks[0]}</span>
          <span>{weeks[weeks.length - 1]}</span>
        </div>
      ) : null}
      {interactive ? (
        <input
          type="range"
          min={0}
          max={Math.max(0, values.length - 1)}
          value={highlighted}
          aria-label="Inspect a week"
          aria-valuetext={weeks && texts ? `Week of ${weeks[highlighted]}: ${texts[highlighted]}` : undefined}
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
