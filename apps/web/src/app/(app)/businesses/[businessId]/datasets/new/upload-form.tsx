"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { postForm } from "@/lib/api/client";
import { ApiProblem, type DatasetDetail } from "@/lib/api/types";
import { useHydrated } from "@/lib/use-hydrated";

const FILES = [
  { kind: "orders", label: "Orders", hint: "One row per order line" },
  { kind: "products", label: "Products", hint: "Product catalogue with cost and list price" },
  { kind: "customers", label: "Customers", hint: "Pseudonymous customer IDs only" },
  { kind: "returns", label: "Returns", hint: "May contain a header row only" },
] as const;

interface UploadError {
  message: string;
  details: string[];
}

export function uploadErrorFrom(error: unknown): UploadError {
  if (error instanceof ApiProblem) {
    return { message: error.message, details: error.details };
  }
  return { message: "Upload failed. Check your connection and try again.", details: [] };
}

export function UploadForm({
  businessId,
  defaultTimezone,
  currency,
}: {
  businessId: string;
  defaultTimezone: string;
  currency: string;
}) {
  const router = useRouter();
  const [error, setError] = useState<UploadError | null>(null);
  const hydrated = useHydrated();
  const [submitting, setSubmitting] = useState(false);
  // Reused across retries of the same submission so a retry never creates a second version.
  const idempotencyKey = useRef<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    idempotencyKey.current ??= crypto.randomUUID().replaceAll("-", "");
    setSubmitting(true);
    setError(null);
    try {
      const dataset = await postForm<DatasetDetail>(
        `/api/v1/businesses/${businessId}/datasets`,
        form,
        idempotencyKey.current,
      );
      router.push(`/businesses/${businessId}/datasets/${dataset.id}`);
    } catch (caught) {
      setError(uploadErrorFrom(caught));
      setSubmitting(false);
    }
  }

  function onFilesChanged() {
    idempotencyKey.current = null; // different files are a different request
  }

  return (
    <form className="grid gap-5" onSubmit={onSubmit} onChange={onFilesChanged} noValidate>
      <div className="grid gap-4 sm:grid-cols-2">
        {FILES.map((file) => (
          <div key={file.kind} className="grid gap-1.5">
            <Label htmlFor={`file-${file.kind}`}>{file.label} (.csv)</Label>
            <Input
              id={`file-${file.kind}`}
              name={file.kind}
              type="file"
              accept=".csv,text/csv"
              required
              aria-describedby={`hint-${file.kind}`}
            />
            <p id={`hint-${file.kind}`} className="text-[13px] text-muted-foreground">
              {file.hint}
            </p>
          </div>
        ))}
      </div>
      <div className="grid gap-1.5 sm:max-w-sm">
        <Label htmlFor="source_timezone">Time zone of times in the files</Label>
        <Input
          id="source_timezone"
          name="source_timezone"
          defaultValue={defaultTimezone}
          aria-describedby="tz-hint"
          autoComplete="off"
        />
        <p id="tz-hint" className="text-[13px] text-muted-foreground">
          IANA name, e.g. Asia/Karachi. Dates are stored in the business time zone. Amounts must be
          in {currency}.
        </p>
      </div>
      {error ? (
        <Alert variant="destructive" role="alert">
          <AlertTitle>{error.message}</AlertTitle>
          {error.details.length > 0 ? (
            <AlertDescription>
              <ul className="list-disc pl-4">
                {error.details.map((detail) => (
                  <li key={detail}>{detail}</li>
                ))}
              </ul>
            </AlertDescription>
          ) : null}
        </Alert>
      ) : null}
      <div>
        <Button type="submit" disabled={!hydrated || submitting}>
          {submitting ? "Uploading…" : "Upload and validate"}
        </Button>
      </div>
    </form>
  );
}
