import Link from "next/link";
import { redirect } from "next/navigation";

import { LogoutButton } from "@/components/logout-button";
import { getMe } from "@/lib/api/server";

/** Authenticated shell: every page below re-validates the session with the backend. */
export default async function AppLayout({ children }: LayoutProps<"/">) {
  const me = await getMe();
  if (!me) {
    redirect("/login");
  }
  return (
    <div className="flex min-h-dvh flex-col">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-background px-3 py-2 text-sm font-medium focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:outline-2 focus:outline-ring"
      >
        Skip to content
      </a>
      <header className="border-b bg-background">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
          <Link href="/" className="text-base font-semibold tracking-tight">
            ROOT
          </Link>
          <nav aria-label="Businesses" className="flex flex-1 flex-wrap gap-3 text-sm">
            {me.memberships.map((membership) => (
              <Link
                key={membership.business_id}
                href={`/businesses/${membership.business_id}`}
                className="underline-offset-4 hover:underline"
              >
                {membership.business_name}
              </Link>
            ))}
          </nav>
          <span className="text-sm text-muted-foreground" data-testid="current-user">
            {me.user.display_name}
          </span>
          <LogoutButton />
        </div>
      </header>
      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 focus:outline-none">
        {children}
      </main>
    </div>
  );
}
