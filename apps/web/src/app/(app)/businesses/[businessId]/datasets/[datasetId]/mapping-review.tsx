"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { postJson } from "@/lib/api/client";
import { ApiProblem, type FileMapping } from "@/lib/api/types";
import { useHydrated } from "@/lib/use-hydrated";

export interface ReviewFile {
  kind: string;
  headers: string[];
  mapping: FileMapping;
}

const NOT_USED = "";

/** Columns a human must decide: proposals (synonym matches) and ambiguous matches. */
export function pendingColumns(mapping: FileMapping): string[] {
  return Object.entries(mapping.columns)
    .filter(([, column]) => column.status === "proposed" || column.status === "ambiguous")
    .map(([name]) => name);
}

export function MappingReview({
  businessId,
  datasetId,
  files,
}: {
  businessId: string;
  datasetId: string;
  files: ReviewFile[];
}) {
  const router = useRouter();
  const pending = files.filter((f) => pendingColumns(f.mapping).length > 0);
  const [choices, setChoices] = useState<Record<string, Record<string, string>>>(() =>
    Object.fromEntries(
      pending.map((f) => [
        f.kind,
        Object.fromEntries(
          pendingColumns(f.mapping).map((name) => [name, f.mapping.columns[name]?.source ?? NOT_USED]),
        ),
      ]),
    ),
  );
  const [error, setError] = useState<{ message: string; details: string[] } | null>(null);
  const hydrated = useHydrated();
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    const body = {
      files: Object.fromEntries(
        Object.entries(choices).map(([kind, columns]) => [
          kind,
          Object.fromEntries(Object.entries(columns).map(([c, v]) => [c, v === NOT_USED ? null : v])),
        ]),
      ),
    };
    try {
      await postJson(`/api/v1/businesses/${businessId}/datasets/${datasetId}/mapping`, body);
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ApiProblem
          ? { message: caught.message, details: caught.details }
          : { message: "Could not save the mapping. Try again.", details: [] },
      );
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="grid gap-6">
      {pending.map((file) => (
        <fieldset key={file.kind} className="grid gap-3">
          <legend className="text-base font-semibold capitalize">{file.kind}.csv</legend>
          {pendingColumns(file.mapping).map((name) => {
            const column = file.mapping.columns[name];
            const id = `map-${file.kind}-${name}`;
            return (
              <div key={name} className="grid gap-1 sm:grid-cols-[14rem_1fr] sm:items-center">
                <label htmlFor={id} className="text-[15px]">
                  <span className="tabular-nums">{name}</span>
                  {column?.required ? " (required)" : " (optional)"}
                  <span className="block text-[13px] text-muted-foreground">
                    {column?.status === "ambiguous"
                      ? `Several columns could match: ${column.candidates.join(", ")}`
                      : `Proposed from header "${column?.source}"`}
                  </span>
                </label>
                <select
                  id={id}
                  className="h-11 rounded-lg border border-input bg-background px-3 text-[15px]"
                  value={choices[file.kind]?.[name] ?? NOT_USED}
                  onChange={(event) =>
                    setChoices((prev) => ({
                      ...prev,
                      [file.kind]: { ...prev[file.kind], [name]: event.target.value },
                    }))
                  }
                >
                  <option value={NOT_USED}>{column?.required ? "Choose a column…" : "Not used"}</option>
                  {file.headers.map((header) => (
                    <option key={header} value={header}>
                      {header}
                    </option>
                  ))}
                </select>
              </div>
            );
          })}
        </fieldset>
      ))}
      {error ? (
        <Alert variant="destructive" role="alert">
          <AlertTitle>{error.message}</AlertTitle>
          {error.details.length > 0 ? (
            <AlertDescription>
              <ul className="list-disc pl-4">
                {error.details.map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ul>
            </AlertDescription>
          ) : null}
        </Alert>
      ) : null}
      <div>
        <Button type="submit" disabled={!hydrated || submitting}>
          {submitting ? "Saving…" : "Confirm mapping and validate"}
        </Button>
      </div>
    </form>
  );
}
