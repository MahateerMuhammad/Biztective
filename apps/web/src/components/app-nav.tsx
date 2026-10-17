"use client";

import { Database, Lightbulb, LayoutDashboard, Plug, SearchCheck } from "lucide-react";

import { BottomNavBar, type NavItem } from "@/components/ui/bottom-nav-bar";
import { cn } from "cn";

/** The five sections of a business. Integrations is for owners and analysts only. */
export function sectionItems(businessId: string, canIntegrate: boolean): NavItem[] {
  const base = `/businesses/${businessId}`;
  const items: NavItem[] = [
    { label: "Business health", icon: LayoutDashboard, href: base, exact: true },
    { label: "Investigations", icon: SearchCheck, href: `${base}/investigations` },
    { label: "Decision center", icon: Lightbulb, href: `${base}/decisions` },
    { label: "Data", icon: Database, href: `${base}/datasets` },
  ];
  if (canIntegrate) items.push({ label: "Integrations", icon: Plug, href: `${base}/integrations` });
  return items;
}

/**
 * Desktop: an inline pill in the top bar with icon and label on every item.
 * Mobile: a floating pill at the bottom where only the current section shows its label.
 * Both are the same `<nav aria-label="Main">`-style landmark, but only one is rendered at a time via CSS,
 * so the unused one is hidden from assistive technology too (display: none).
 */
export function AppNav({ businessId, canIntegrate }: { businessId: string; canIntegrate: boolean }) {
  const items = sectionItems(businessId, canIntegrate);
  return (
    <>
      <BottomNavBar items={items} showAllLabels className={cn("hidden md:flex")} />
      <BottomNavBar items={items} stickyBottom className="md:hidden" />
    </>
  );
}
