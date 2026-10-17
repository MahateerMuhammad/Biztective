"use client";

import Link, { useLinkStatus } from "next/link";
import { usePathname } from "next/navigation";
import { motion, useReducedMotion } from "motion/react";
import { Loader2, type LucideIcon } from "lucide-react";

import { cn } from "cn";

/**
 * Pill navigation, adapted from the BottomNavBar starting point: real links instead of buttons, the
 * active item comes from the URL, and `motion` comes from `motion/react`. The label width animates
 * to `auto` so long names ("Decision center") are never cut off.
 */
export type NavItem = {
  label: string;
  icon: LucideIcon;
  href: string;
  /** Match only this exact path (an index route); otherwise this path and everything below it. */
  exact?: boolean;
};

export function isActive(pathname: string, item: NavItem): boolean {
  return item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`);
}

type BottomNavBarProps = {
  items: NavItem[];
  className?: string;
  /** Float at the bottom of the screen, labels only on the active item (mobile). */
  stickyBottom?: boolean;
  /** Show every label (inline top bar, desktop). Ignored when `stickyBottom`. */
  showAllLabels?: boolean;
};

export function BottomNavBar({ items, className, stickyBottom = false, showAllLabels = false }: BottomNavBarProps) {
  const pathname = usePathname();
  const reduce = useReducedMotion();
  const everyLabel = showAllLabels && !stickyBottom;

  return (
    <motion.nav
      aria-label="Main"
      initial={reduce ? false : { scale: 0.92, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ type: "spring", stiffness: 300, damping: 26 }}
      className={cn(
        "flex items-center gap-1 rounded-full border border-border bg-card p-1.5 text-card-foreground",
        stickyBottom && "fixed inset-x-0 bottom-[max(1rem,env(safe-area-inset-bottom))] z-30 mx-auto w-fit max-w-[calc(100vw-2rem)] shadow-xl",
        className,
      )}
    >
      {items.map((item) => {
        const Icon = item.icon;
        const active = isActive(pathname, item);
        const open = everyLabel || active;
        return (
          <div key={item.href} className="flex">
            <Link
              href={item.href}
              aria-current={active ? "page" : undefined}
              aria-label={open ? undefined : item.label}
              className={cn(
                "relative flex h-11 min-w-11 items-center justify-center rounded-full px-3 text-[15px] font-medium outline-none transition-[color,background-color,transform] duration-200 active:scale-[0.97] motion-reduce:transition-none motion-reduce:active:scale-100 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card",
                active ? "bg-primary/10 text-primary dark:bg-primary/15" : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <NavIcon icon={Icon} />
              <motion.span
                initial={false}
                animate={
                  open
                    ? { width: "auto", opacity: 1, marginLeft: 8 }
                    : { width: 0, opacity: 0, marginLeft: 0 }
                }
                transition={
                  reduce
                    ? { duration: 0 }
                    : { width: { type: "spring", stiffness: 350, damping: 32 }, opacity: { duration: 0.19 }, marginLeft: { duration: 0.19 } }
                }
                className="overflow-hidden whitespace-nowrap select-none"
                aria-hidden={open ? undefined : true}
              >
                {item.label}
              </motion.span>
            </Link>
          </div>
        );
      })}
    </motion.nav>
  );
}

export default BottomNavBar;

/** Shows a spinner in place of the icon while the clicked section is loading (instant feedback). */
function NavIcon({ icon: Icon }: { icon: NavItem["icon"] }) {
  const { pending } = useLinkStatus();
  return pending ? (
    <Loader2 size={22} strokeWidth={2} aria-hidden="true" className="shrink-0 animate-spin motion-reduce:animate-none" />
  ) : (
    <Icon size={22} strokeWidth={2} aria-hidden="true" className="shrink-0" />
  );
}
