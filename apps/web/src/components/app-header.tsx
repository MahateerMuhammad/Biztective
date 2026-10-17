"use client";

import { ChevronDown, LogOut } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AppNav } from "@/components/app-nav";
import { signOut } from "@/components/logout-button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SunMoonThemeToggle } from "@/components/ui/sun-moon-theme-toggle";

const ROLES = { owner: "Owner", analyst: "Analyst", viewer: "Viewer" } as const;

export interface HeaderBusiness {
  id: string;
  name: string;
  role: keyof typeof ROLES;
  currency: string;
  timezone: string;
}

/**
 * The one top bar: wordmark and business name on the left, the section navigation in the middle
 * (desktop), theme toggle and user menu on the right. On small screens the navigation floats at the bottom.
 */
export function AppHeader({
  userName,
  business,
  others,
}: {
  userName: string;
  business?: HeaderBusiness;
  /** Other businesses the user can switch to. */
  others: { id: string; name: string }[];
}) {
  const router = useRouter();
  const [failed, setFailed] = useState(false);
  const initial = (userName.trim()[0] ?? "?").toUpperCase();
  const role = business ? ROLES[business.role] : null;

  async function logout() {
    setFailed(false);
    if (!(await signOut())) {
      setFailed(true);
      return;
    }
    router.replace("/login");
    router.refresh();
  }

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/70">
      <div className="mx-auto flex max-w-[80rem] items-center gap-4 px-4 py-2.5 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <Link
            href="/"
            className="flex shrink-0 items-center gap-2 rounded-md text-lg font-semibold tracking-tight outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Image src="/logo.png" alt="" aria-hidden="true" width={640} height={443} priority className="h-7 w-auto" />
            Biztective
          </Link>
          {business ? (
            <>
              <span aria-hidden="true" className="h-6 w-px shrink-0 bg-border" />
              <span className="truncate text-base font-medium text-muted-foreground" data-testid="current-business">
                {business.name}
              </span>
            </>
          ) : null}
        </div>

        {business ? (
          <div className="mx-auto hidden md:block">
            <AppNav businessId={business.id} canIntegrate={business.role !== "viewer"} />
          </div>
        ) : (
          <div className="mx-auto" />
        )}

        <div className="ml-auto flex shrink-0 items-center gap-2 md:ml-0">
          <SunMoonThemeToggle storageKey="biztective-theme" size={36} duration={900} />
          <DropdownMenu>
            <DropdownMenuTrigger
              className="flex h-11 items-center gap-2 rounded-full border border-border bg-card pr-3 pl-1.5 text-[15px] font-medium outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring aria-expanded:bg-muted"
              data-testid="current-user"
            >
              <span aria-hidden="true" className="flex size-8 items-center justify-center rounded-full bg-primary/15 text-sm font-semibold text-primary">
                {initial}
              </span>
              <span className="hidden max-w-32 truncate sm:inline">{userName}</span>
              <span className="sr-only sm:hidden">{userName}, account menu</span>
              <ChevronDown aria-hidden="true" className="size-4 text-muted-foreground" />
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuGroup>
                <DropdownMenuLabel>
                  <span className="block text-base font-semibold text-foreground">{userName}</span>
                  {business && role ? (
                    <span className="block">
                      {role} · {business.currency} · {business.timezone}
                    </span>
                  ) : null}
                </DropdownMenuLabel>
              </DropdownMenuGroup>
              {others.length > 0 ? (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuGroup>
                    <DropdownMenuLabel>Switch business</DropdownMenuLabel>
                    {others.map((b) => (
                      <DropdownMenuItem key={b.id} render={<Link href={`/businesses/${b.id}`} />}>
                        {b.name}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuGroup>
                </>
              ) : null}
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={logout}>
                <LogOut aria-hidden="true" />
                Sign out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      {failed ? (
        <p role="alert" className="mx-auto max-w-[80rem] px-4 pb-2 text-sm text-destructive sm:px-6">
          We couldn&apos;t sign you out. Try again.
        </p>
      ) : null}
    </header>
  );
}
