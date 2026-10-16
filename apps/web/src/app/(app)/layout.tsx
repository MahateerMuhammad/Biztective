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
      <header className="border-b">
        <div className="mx-auto flex max-w-5xl items-center gap-4 px-4 py-3">
          <Link href="/" className="font-semibold tracking-tight">
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
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6">{children}</main>
    </div>
  );
}
