import { redirect } from "next/navigation";

import { AppHeader } from "@/components/app-header";
import { EmptyState } from "@/components/page";
import { getMe } from "@/lib/api/server";

export default async function HomePage() {
  const me = await getMe();
  if (!me) {
    redirect("/login");
  }
  const first = me.memberships[0];
  if (first) {
    redirect(`/businesses/${first.business_id}`);
  }
  return (
    <>
      <AppHeader userName={me.user.display_name} others={[]} />
      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-[80rem] flex-1 px-4 py-8 sm:px-6 focus:outline-none">
        <EmptyState title="No businesses yet">Your account is not a member of any business. Ask a business owner for access.</EmptyState>
      </main>
    </>
  );
}
