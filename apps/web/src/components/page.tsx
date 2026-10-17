import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { InfoTip } from "@/components/ui/info-tip";
import { cn } from "cn";

import { CHIP, DOT, TEXT, type Tone } from "./health/primitives";

/* One page pattern for the whole app. Pages compose these instead of restyling headings, cards and
 * empty states by hand, so type sizes, spacing and colours cannot drift between screens. */

export function PageHeader({
  title,
  subtitle,
  action,
  back,
}: {
  title: ReactNode;
  /** One short line. Anything longer belongs in an InfoTip or a SectionCard. */
  subtitle?: ReactNode;
  /** The page's one primary action. */
  action?: ReactNode;
  back?: ReactNode;
}) {
  return (
    <header className="grid gap-2">
      {back ? <div className="text-[15px]">{back}</div> : null}
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="grid min-w-0 gap-1">
          <h1 className="text-[1.875rem] leading-tight font-semibold tracking-tight text-balance">{title}</h1>
          {subtitle ? (
            <p className="text-[15px] text-muted-foreground" role="status">
              {subtitle}
            </p>
          ) : null}
        </div>
        {action ? <div className="flex flex-wrap items-center gap-2">{action}</div> : null}
      </div>
    </header>
  );
}

export function SectionCard({
  title,
  help,
  tip,
  action,
  children,
  className,
  id,
}: {
  title?: ReactNode;
  /** One line under the title. */
  help?: ReactNode;
  /** Longer explanation, kept out of the way behind an "i". */
  tip?: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section id={id} className={cn("grid gap-4 rounded-xl bg-card p-4 text-card-foreground sm:p-6 ring-1 ring-foreground/10", className)}>
      {title || action ? (
        <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div className="grid min-w-0 gap-1">
            {title ? (
              <h2 className="flex items-center gap-1 text-base leading-snug font-semibold">
                {title}
                {tip ? <InfoTip label={`More about ${typeof title === "string" ? title : "this section"}`}>{tip}</InfoTip> : null}
              </h2>
            ) : null}
            {help ? <p className="text-[15px] text-muted-foreground">{help}</p> : null}
          </div>
          {action}
        </header>
      ) : null}
      {children}
    </section>
  );
}

export function StatCard({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  return (
    <div className="grid gap-1 rounded-xl bg-card p-6 ring-1 ring-foreground/10">
      <p className="text-sm font-medium text-muted-foreground">{label}</p>
      <p className={cn("text-4xl leading-tight font-semibold tracking-tight tabular-nums", tone ? TEXT[tone] : "text-foreground")}>{value}</p>
      {hint ? <p className="text-[15px] text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

/** Status in words and colour together (WCAG 1.4.1). */
export function StatusChip({ tone = "idle", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[13px] font-medium whitespace-nowrap", CHIP[tone], className)}>
      <span aria-hidden="true" className={cn("size-1.5 rounded-full", DOT[tone])} />
      {children}
    </span>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  children,
  action,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 px-4 py-8 text-center", className)}>
      {Icon ? <Icon aria-hidden="true" className="size-9 text-muted-foreground" strokeWidth={1.5} /> : null}
      <p className="text-base font-semibold text-foreground">{title}</p>
      {children ? <p className="max-w-[44ch] text-[15px] text-muted-foreground">{children}</p> : null}
      {action}
    </div>
  );
}

/** Secondary detail that is one click away, never removed. */
export function Disclosure({ summary, children, className }: { summary: ReactNode; children: ReactNode; className?: string }) {
  return (
    <details className={cn("group text-[15px]", className)}>
      <summary className="inline-flex min-h-11 cursor-pointer items-center rounded-md text-sm font-medium text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring">
        {summary}
      </summary>
      <div className="pt-1 text-muted-foreground">{children}</div>
    </details>
  );
}

export function LoadingSkeleton({ className, lines = 3 }: { className?: string; lines?: number }) {
  return (
    <div role="status" aria-label="Loading" className={cn("grid gap-3", className)}>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="h-5 animate-pulse rounded-md bg-skeleton motion-reduce:animate-none" style={{ width: `${100 - i * 12}%` }} />
      ))}
      <span className="sr-only">Loading…</span>
    </div>
  );
}

/** A clickable list row: one link, chevron on the right. */
export const rowLink =
  "group flex min-h-11 items-center gap-3 rounded-lg px-2 py-2 -mx-2 text-foreground outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring";

/* Tables: few columns, friendly values, one link per row (the first cell's link covers the row). */
export const tableWrap = "overflow-x-auto";
export const tableClass = "w-full text-left";
export const thClass = "whitespace-nowrap sticky top-0 bg-card py-3 pr-4 text-sm font-medium text-muted-foreground";
export const trClass = "relative border-t border-border first:border-t-0 hover:bg-muted/60";
export const tdClass = "py-3.5 pr-4 text-[15px] align-middle";
export const rowLinkClass =
  "whitespace-nowrap font-medium text-foreground outline-none after:absolute after:inset-0 after:content-[''] focus-visible:after:ring-2 focus-visible:after:ring-ring focus-visible:after:ring-inset";
