import { notFound } from "next/navigation";

import { NavLink } from "@/components/nav-link";
import { getBusiness } from "@/lib/api/server";

const ROLES = { owner: "Owner", analyst: "Analyst", viewer: "Viewer" } as const;

/** Business shell: the three main screens are one click apart from anywhere in a business. */
export default async function BusinessLayout({ children, params }: LayoutProps<"/businesses/[businessId]">) {
  const { businessId } = await params;
  const business = await getBusiness(businessId);
  if (!business) notFound(); // not a member: the same answer as a missing business
  const base = `/businesses/${businessId}`;
  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b pb-3">
        <div>
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {ROLES[business.role]} · {business.currency} · {business.timezone}
          </p>
          <p className="text-lg font-semibold tracking-tight">{business.name}</p>
        </div>
        <nav aria-label="Sections" className="-mx-1 flex flex-wrap gap-1">
          <NavLink href={base} exact>
            Business health
          </NavLink>
          <NavLink href={`${base}/investigations`}>Investigations</NavLink>
          <NavLink href={`${base}/decisions`}>Decision center</NavLink>
          <NavLink href={`${base}/datasets`}>Data</NavLink>
          {business.role !== "viewer" ? <NavLink href={`${base}/integrations`}>Integrations</NavLink> : null}
        </nav>
      </div>
      {children}
    </div>
  );
}
