import { notFound } from "next/navigation";

import { AppHeader } from "@/components/app-header";
import { getBusiness, getMe } from "@/lib/api/server";

/** Business shell: the single top bar (with the section navigation) and the page area. */
export default async function BusinessLayout({ children, params }: LayoutProps<"/businesses/[businessId]">) {
  const { businessId } = await params;
  const [business, me] = await Promise.all([getBusiness(businessId), getMe()]);
  if (!business || !me) notFound(); // not a member: the same answer as a missing business
  return (
    <>
      <AppHeader
        userName={me.user.display_name}
        business={{ id: business.id, name: business.name, role: business.role, currency: business.currency, timezone: business.timezone }}
        others={me.memberships.filter((m) => m.business_id !== businessId).map((m) => ({ id: m.business_id, name: m.business_name }))}
      />
      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-[80rem] flex-1 px-4 pt-8 pb-28 focus:outline-none sm:px-6 md:pb-12">
        {children}
      </main>
    </>
  );
}
