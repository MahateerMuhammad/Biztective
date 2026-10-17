import { redirect } from "next/navigation";

import { getMe } from "@/lib/api/server";

/**
 * Authenticated shell: every page below re-validates the session with the backend. The top bar is
 * rendered by the pages' own layouts (the business layout knows the business), so there is only one.
 */
export default async function AppLayout({ children }: LayoutProps<"/">) {
  const me = await getMe();
  if (!me) {
    redirect("/login");
  }
  return (
    <div className="flex min-h-dvh flex-col">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-background px-3 py-2 text-[15px] font-medium focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:outline-2 focus:outline-ring"
      >
        Skip to content
      </a>
      {children}
    </div>
  );
}
