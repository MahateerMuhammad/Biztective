import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getBusiness } from "@/lib/api/server";

import { UploadForm } from "./upload-form";

export const metadata: Metadata = { title: "Upload dataset" };

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
      <div>
        <Link href={`/businesses/${businessId}`} className="text-sm underline-offset-4 hover:underline">
          ← {business.name}
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">Upload dataset</h1>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>
            <h2 className="text-base font-semibold">Four CSV files</h2>
          </CardTitle>
          <CardDescription>
            Each upload becomes a new, immutable dataset version. Uncertain column matches wait for
            your confirmation before anything is loaded. Columns that look like personal data
            (names, emails, phones, addresses) are dropped and never stored.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {canUpload ? (
            <UploadForm
              businessId={businessId}
              defaultTimezone={business.timezone}
              currency={business.currency}
            />
          ) : (
            <p className="text-sm text-muted-foreground">Your role can view datasets but not upload them.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
