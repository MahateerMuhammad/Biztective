import { redirect } from "next/navigation";

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
    <section>
      <h1 className="text-xl font-semibold">No businesses yet</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Your account is not a member of any business. Ask a business owner for access.
      </p>
    </section>
  );
}
