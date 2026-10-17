import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { PageHeader, SectionCard } from "@/components/page";
import { getBusiness } from "@/lib/api/server";

import { UploadForm } from "./upload-form";

export const metadata: Metadata = { title: "Upload data" };

export default async function NewDatasetPage({
  params,
}: PageProps<"/businesses/[businessId]/datasets/new">) {
  const { businessId } = await params;
  const business = await getBusiness(businessId);
  if (!business) {
    notFound();
  }
  const canUpload = business.role === "owner" || business.role === "analyst";

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Upload data"
        subtitle="Four CSV files make one new version of your data."
        back={
          <Link href={`/businesses/${businessId}/datasets`} className="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
            ← Data
          </Link>
        }
      />
      <SectionCard
        title="Your files"
        help="Unsure column matches wait for your confirmation before anything is loaded."
        tip="Each upload becomes a new version that is never changed afterwards. Columns that look like personal data (names, emails, phones, addresses) are dropped and never stored."
      >
        {canUpload ? (
          <UploadForm businessId={businessId} defaultTimezone={business.timezone} currency={business.currency} />
        ) : (
          <p className="text-[15px] text-muted-foreground">Your role can view data but not upload it.</p>
        )}
      </SectionCard>
      <SectionCard title="Test files (synthetic data)" help="A six-month sample shop for trying the upload. Not real business data.">
        <ul className="flex flex-wrap gap-4 text-[15px]">
          {["orders", "products", "customers", "returns"].map((kind) => (
            <li key={kind}>
              <a href={`/samples/${kind}.csv`} download className="underline underline-offset-4 hover:text-foreground">
                {kind}.csv
              </a>
            </li>
          ))}
        </ul>
      </SectionCard>
    </div>
  );
}
